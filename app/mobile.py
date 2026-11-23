"""One phone snapshot and one question per request, without changing the host camera."""
import asyncio
import base64
from pathlib import Path
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from .assistant import Conversation, Turn
from .device import Recording
from .nav_types import NavError
from .providers import LatestCamera, NotConfigured
from .speech import SpeechError
from .vision import VisionError

MAX_JPEG = 1024 * 1024
MAX_PCM = 320000
MAX_TEXT_BYTES = 2000


def mobile_router(service):
    router = APIRouter(prefix='/api/mobile')
    agent = Conversation(service)

    def require_access(request):
        # The app-wide Basic auth middleware validates credentials before routing.
        if not service.config.web_access_password or request.url.scheme != 'https':
            raise HTTPException(403, '手机接入需在电脑配置 HTTPS 和访问口令')

    @router.get('/status')
    async def status(request: Request):
        require_access(request)
        return {'camera_input': 'phone_snapshot', 'asr': service.speech.asr_enabled}

    @router.get('/apk')
    async def apk(request: Request):
        require_access(request)
        artifact = Path(__file__).resolve().parents[1] / 'artifacts/android/3800-mobile.apk'
        if not artifact.is_file():
            raise HTTPException(404, '请先在电脑运行 android/build.sh 构建安装包')
        return FileResponse(artifact, media_type='application/vnd.android.package-archive',
                            filename='3800-mobile.apk')

    @router.post('/ask')
    async def ask(request: Request, input: Literal['text', 'voice'] = 'text',
                  allow_cloud: bool = False):
        require_access(request)
        if request.headers.get('content-type') != 'application/octet-stream':
            raise HTTPException(415, '需要 JPEG 和问题数据')
        if agent.turn_lock.locked():
            raise HTTPException(429, '上一条手机问答正在处理')
        image_limit = min(MAX_JPEG, service.config.upload_max_bytes)
        question_limit = MAX_PCM if input == 'voice' else MAX_TEXT_BYTES
        async with agent.turn_lock:
            data = bytearray()

            async def receive():
                async for chunk in request.stream():
                    if len(data) + len(chunk) > 4 + image_limit + question_limit:
                        raise HTTPException(413, '画面或问题超过大小限制')
                    data.extend(chunk)

            try:
                await asyncio.wait_for(receive(), 10)
            except asyncio.TimeoutError:
                raise HTTPException(408, '上传手机画面超时') from None
            size = int.from_bytes(data[:4], 'big')
            if not 4 <= size <= image_limit or size + 4 >= len(data):
                raise HTTPException(422, '缺少完整的 JPEG 或问题')
            image = bytes(data[4:4 + size])
            payload = bytes(data[4 + size:])
            if len(payload) > question_limit:
                raise HTTPException(413, '问题超过大小限制')
            if not image.startswith(b'\xff\xd8'):
                raise HTTPException(422, '手机画面必须为 JPEG')
            if input == 'voice' and (len(payload) < 6400 or len(payload) % 2):
                raise HTTPException(422, '录音太短或格式不完整')
            if input == 'voice' and not service.speech.asr_enabled:
                raise HTTPException(409, '电脑未启用本地语音识别')

            try:
                frame = await asyncio.to_thread(
                    LatestCamera().set_image, image, 'phone_snapshot', False,
                )
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                raise HTTPException(422, '眼镜画面无法解码') from None

            try:
                text = (await service.speech.transcribe(Recording(0, payload, 16000, time.monotonic()))
                        if input == 'voice' else payload.decode('utf-8').strip())
            except UnicodeDecodeError:
                raise HTTPException(422, '文字问题必须为 UTF-8') from None
            except SpeechError as exc:
                raise HTTPException(409, str(exc)) from None
            if not text or len(text) > 500:
                raise HTTPException(422, '请输入 1 至 500 字的问题')
            try:
                reply = await agent.answer(Turn(text=text, allow_frame=allow_cloud), frame)
            except (NavError, NotConfigured, VisionError) as exc:
                raise HTTPException(409, str(exc)) from None
            except asyncio.TimeoutError:
                raise HTTPException(504, '电脑视觉模型响应超时') from None
            audio = None
            if service.speech.tts_enabled and not service.device.recordings.current:
                try:
                    wav = await service.speech.synthesize(reply)
                    if len(wav) <= 4 * 1024 * 1024:
                        audio = base64.b64encode(wav).decode('ascii')
                except SpeechError:
                    pass
            return {'heard': text, 'reply': reply, 'frame_id': frame.frame_id, 'audio_wav': audio}

    return router
