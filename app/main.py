import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import time
from typing import Literal
import warnings
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator
from .config import Settings
from .domain import DistanceInput
from .providers import LatestCamera, NotConfigured
from .service import Service
from .speech import SpeechError
from .vision import build_vision
from .nav_api import nav_router
from .assistant import assistant_router

WEB = Path(__file__).parent / 'web'


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    frame_id: str | None = Field(..., min_length=1, max_length=64)
    allow_live_upload: bool = False

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('问题不能为空')
        return value.strip()


class Mode(BaseModel):
    value: str = Field(max_length=30)


class Transcription(BaseModel):
    stream_id: int = Field(ge=0, le=4294967295)


def create_app(settings: Settings | None = None, vision=None):
    config = settings or Settings.from_env()
    service = Service(config, vision)
    # At most one decode in progress: reject concurrent uploads rather than queue media.
    upload_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format='%(message)s')
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title='AIAA 3800 离线软件原型', lifespan=lifespan)
    app.state.service = service

    @app.middleware('http')
    async def privacy_and_local_use(request: Request, call_next):
        # No CORS; browser mutations require same origin, including loopback use.
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != str(request.base_url).rstrip('/'):
            return Response('跨来源写入请求已拒绝', status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/')
    async def index():
        return FileResponse(WEB / 'index.html')

    app.include_router(nav_router(service))
    app.include_router(assistant_router(service))

    @app.get('/assistant')
    async def assistant_page():
        return FileResponse(WEB / 'assistant.html')

    @app.get('/demo')
    async def demo_page():
        return FileResponse(WEB / 'demo.html')

    @app.get('/navigation')
    async def navigation_page():
        return FileResponse(WEB / 'navigation.html')

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'prototype': True}

    @app.get('/api/state')
    async def state():
        return await service.state()

    @app.get('/api/frame')
    async def frame():
        try:
            current = await service.camera.snapshot()
        except NotConfigured as exc:
            raise HTTPException(409, str(exc)) from None
        return Response(current.content, media_type='image/jpeg', headers={'X-Frame-ID': current.frame_id})

    @app.get('/api/camera/live-frame')
    async def live_frame(after: str = Query('', max_length=64)):
        camera = service.camera
        deadline = time.monotonic() + 2
        while camera.mode == 'esp32':
            current = camera.frame
            if (current and not current.static and current.frame_id != after
                    and time.monotonic() - current.received_mono <= config.frame_ttl_s):
                age_ms = round((time.monotonic() - current.received_mono) * 1000)
                return Response(current.content, media_type='image/jpeg', headers={
                    'X-Frame-ID': current.frame_id, 'X-Frame-Age-Ms': str(age_ms),
                })
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(camera.changed.wait(), remaining)
            except asyncio.TimeoutError:
                break
        return Response(status_code=204)

    @app.post('/api/recording/transcribe')
    async def transcribe(body: Transcription):
        try:
            return await service.transcribe(body.stream_id)
        except SpeechError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get('/api/audio/{request_id}')
    async def audio(request_id: str):
        clip = service.audio_clip
        if not clip or clip[0] != request_id or service.device.recordings.current:
            raise HTTPException(404, '音频不可用')
        return Response(clip[1], media_type='audio/wav')

    @app.post('/api/image')
    async def upload(request: Request):
        if upload_lock.locked():
            raise HTTPException(429, '另一张图片正在处理，请稍后再试')
        if request.headers.get('content-type', '').split(';')[0] not in ('image/jpeg', 'image/png'):
            raise HTTPException(415, '请直接上传 JPEG / PNG 图片内容')
        async with upload_lock:
            data = bytearray()
            try:
                async with _UploadDeadline(10):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > config.upload_max_bytes:
                            raise HTTPException(413, '图片超过大小限制')
                        data.extend(chunk)
            except asyncio.TimeoutError:
                raise HTTPException(408, '上传超时') from None
            def decode():
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    return LatestCamera().set_image(bytes(data), 'upload', False)
            try:
                current = await asyncio.to_thread(decode)
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise HTTPException(422, '图片损坏、格式不支持或尺寸超过1200万像素') from None
            service.camera.frame = current
            service.camera.mode = 'upload'
            return current.public()

    @app.post('/api/mode/{module}')
    async def mode(module: Literal['camera', 'telemetry', 'vision'], body: Mode):
        options = {'camera': ('mock', 'upload', 'esp32'), 'telemetry': ('mock', 'esp32'), 'vision': ('mock', 'unconfigured', 'openai_compatible', 'ollama')}
        if body.value not in options[module]:
            raise HTTPException(422, '不支持的模式')
        if module == 'camera':
            if body.value == 'mock':
                service.camera.mock()
            else:
                service.camera.frame = None
                service.camera.mode = body.value
                service.camera.message = (
                    ('等待实时帧' if config.esp32_host else '请设置 ESP32_HOST')
                    if body.value == 'esp32' else '请选择本地图片'
                )
        elif module == 'vision':
            await service.cancel()
            service.vision_name = body.value
            service.vision = build_vision(config, body.value)
        else:
            if body.value != service.telemetry_mode:
                service.device.recordings.latest = None
                service.device.recordings.abort(None)
            service.telemetry_mode = body.value
            from .domain import DistanceSample, utc_now
            import time
            service.sample = DistanceSample(DistanceInput(status='not_installed' if body.value == 'mock' else 'unconfigured'), utc_now(), time.monotonic(), body.value)
            await service.evaluate_distance()
        return await service.state()

    @app.post('/api/distance')
    async def distance(body: DistanceInput):
        try:
            return await service.distance(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post('/api/questions', status_code=202)
    async def question(body: Question):
        try:
            return await service.submit(body.question, body.frame_id, body.allow_live_upload)
        except (NotConfigured, ValueError) as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post('/api/cancel')
    async def cancel():
        await service.cancel()
        return {'status': 'cancelled'}

    app.mount('/static', StaticFiles(directory=WEB), name='static')
    return app


class _UploadDeadline:
    """Python 3.10 compatible whole-stream timeout, without background queues."""
    def __init__(self, seconds):
        self.seconds = seconds
        self.expired = False

    async def __aenter__(self):
        self.task = asyncio.current_task()
        def expire():
            self.expired = True
            self.task.cancel()
        self.handle = asyncio.get_running_loop().call_later(self.seconds, expire)

    async def __aexit__(self, kind, value, tb):
        self.handle.cancel()
        if kind is asyncio.CancelledError and self.expired:
            raise asyncio.TimeoutError from None
        return False


app = create_app()
