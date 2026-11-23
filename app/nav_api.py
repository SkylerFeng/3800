"""Navigation controls use a same-origin CSRF token, not user authentication."""
import asyncio
import secrets
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from .device import Recording
from .maps import MOCK_START, MOCK_CORNER, MOCK_END
from .domain import utc_now
from .nav_types import (NavError, PositionInput, SearchInput, PlanInput, MapMode, LocationFault)
from .speech import SpeechError
from pydantic import BaseModel
from typing import Literal


class MockFix(BaseModel):
    point: Literal['start', 'middle', 'corner', 'north', 'end', 'off_route']


def nav_router(service):
    router=APIRouter(prefix='/api/navigation')
    nav=service.navigation
    speech_lock = asyncio.Lock()
    voice_lock = asyncio.Lock()

    async def authorized(request):
        token=request.headers.get('X-Navigation-Token','')
        if not token or not secrets.compare_digest(token,nav.token):
            raise HTTPException(403,'导航会话令牌无效，请刷新本机页面')

    @router.get('/session')
    async def session(request: Request):
        # Same-origin CSRF token, NOT user authentication. Trusted single-user LAN only.
        return {'token':nav.token}

    @router.get('/state')
    async def state(request: Request):
        await authorized(request)
        return service.navigation_state()

    @router.post('/speech')
    async def speech(request: Request):
        await authorized(request)
        if speech_lock.locked():
            raise HTTPException(409,'导航播报正在生成')
        async with speech_lock:
            state = service.navigation_state()
            instruction = state['instruction']
            if not instruction or state['recording_active'] or not state['speech_enabled']:
                raise HTTPException(409,'当前没有可播放的导航提示')
            route_id = state['route']['route_id']
            step_index = state['step_index']
            try:
                audio = await service.speech.synthesize(instruction[:300])
            except SpeechError as exc:
                raise HTTPException(409,str(exc)) from None
            latest = service.navigation_state()
            if (latest['instruction'] != instruction or latest['recording_active']
                    or (latest['route'] or {}).get('route_id') != route_id
                    or latest['step_index'] != step_index):
                raise HTTPException(409,'导航提示已经更新')
            return Response(audio, media_type='audio/wav')

    @router.post('/speech-test')
    async def speech_test(request: Request):
        await authorized(request)
        if service.device.recordings.current:
            raise HTTPException(409, '录音期间暂停播放')
        if speech_lock.locked():
            raise HTTPException(429, '正在生成语音，请稍候')
        async with speech_lock:
            try:
                audio = await service.speech.synthesize('这是一段普通话试听。前方二十米后左转。')
            except SpeechError as exc:
                raise HTTPException(409, str(exc)) from None
            return Response(audio, media_type='audio/wav')

    async def execute(request,operation):
        await authorized(request)
        try:
            return await operation()
        except NavError as exc:
            raise HTTPException(409,str(exc)) from None

    @router.post('/voice-destination')
    async def voice_destination(request: Request):
        await authorized(request)
        if not service.speech.asr_enabled:
            raise HTTPException(409, '请先配置本地语音识别')
        if request.headers.get('content-type') != 'application/octet-stream':
            raise HTTPException(415, '录音格式应为 16 kHz PCM')
        if voice_lock.locked():
            raise HTTPException(429, '上一段录音正在识别')
        async with voice_lock:
            pcm = bytearray()
            async def receive():
                async for chunk in request.stream():
                    if len(pcm) + len(chunk) > 320000:
                        raise HTTPException(413, '语音输入最长 10 秒')
                    pcm.extend(chunk)
            try:
                await asyncio.wait_for(receive(), 8)
                if len(pcm) < 6400 or len(pcm) % 2:
                    raise HTTPException(422, '录音太短或数据不完整')
                text = await service.speech.transcribe(Recording(0, bytes(pcm), 16000, time.monotonic()))
                return {'text': text}
            except asyncio.TimeoutError:
                raise HTTPException(408, '录音上传超时') from None
            except SpeechError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post('/mode')
    async def mode(request:Request,body:MapMode):
        async def run():
            await nav.set_mode(body.value)
            return nav.state()
        return await execute(request,run)

    @router.post('/position')
    async def position(request:Request,body:PositionInput):
        return await execute(request,lambda:nav.position(body))

    @router.post('/location-fault')
    async def fault(request:Request,body:LocationFault):
        await authorized(request)
        return nav.fault(body.status)

    @router.post('/search')
    async def search(request:Request,body:SearchInput):
        return await execute(request,lambda:nav.search(body.query,body.region,body.page))

    @router.post('/plan')
    async def plan(request:Request,body:PlanInput):
        return await execute(request,lambda:nav.plan(body.search_id,body.candidate_id))

    @router.post('/start')
    async def start(request:Request):
        await authorized(request)
        try:
            return nav.start()
        except NavError as exc:
            raise HTTPException(409,str(exc)) from None

    @router.post('/pause')
    async def pause(request:Request):
        await authorized(request)
        return nav.pause()

    @router.post('/stop')
    async def stop(request:Request):
        await authorized(request)
        return nav.stop()

    @router.post('/mock-position')
    async def mock_position(request:Request,body:MockFix):
        await authorized(request)
        if nav.mode!='mock':
            raise HTTPException(409,'真实地图模式拒绝模拟位置')
        points={'start':MOCK_START, 'middle':MOCK_START.model_copy(update={'longitude':116.4005}),
                'corner':MOCK_CORNER,'north':MOCK_CORNER.model_copy(update={'latitude':39.9005}),
                'end':MOCK_END,'off_route':MOCK_CORNER.model_copy(update={'latitude':39.899})}
        point=points[body.point]
        body=PositionInput(**point.model_dump(),source='mock',accuracy_m=3,captured_at=utc_now())
        return await execute(request,lambda:nav.position(body))

    return router
