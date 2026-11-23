"""Conversation and tool calls for the foreground 3800 voice assistant."""
import asyncio
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .assistant_intents import DESTINATION, NUMBER, plan_actions
from .device import Recording
from .domain import utc_now
from .maps import MOCK_START
from .nav_types import NavError, PositionInput
from .providers import NotConfigured
from .speech import SpeechError
from .vision import VisionError, vision_status

HELP = '可以问前方障碍有多远、杯子在哪里，或说步行去图书馆。也可以先问距离，再问画面。'
ORDINALS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5}


class Turn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    region: str = Field(default='', max_length=40)
    allow_frame: bool = False
    allow_location: bool = False


class Conversation:
    def __init__(self, service):
        self.service = service
        self.nav = service.navigation
        self.turn_lock = asyncio.Lock()
        self.owner = None
        self.expires = 0.
        self.search_id = None
        self.route_id = None
        self.query = ''
        self.region = ''
        self.page = 1
        self.offset = 0
        self.last_reply = ''
        self.clip = None

    def begin(self):
        if self.owner:
            self.end()
        self.owner = secrets.token_urlsafe(24)
        self.expires = time.monotonic() + self.service.config.assistant_session_s
        return self.owner

    def end(self):
        if ((self.search_id and self.nav.search_id == self.search_id)
                or (self.nav.route and self.nav.route.route_id == self.route_id)):
            self.nav.stop()
        self.owner = self.search_id = self.route_id = self.clip = None
        self.last_reply = ''
        self.expires = 0.

    def check(self, owner):
        if self.owner and time.monotonic() >= self.expires:
            self.end()
        if not self.owner or not owner.isascii() or not secrets.compare_digest(owner, self.owner):
            raise HTTPException(403, '请重新开始助手会话')

    def pending_candidates(self):
        return (self.search_id is not None and self.search_id == self.nav.search_id
                and not self.nav.used_search)

    def owns_route(self):
        return self.nav.route is not None and self.nav.route.route_id == self.route_id

    async def reset_mock_position(self):
        if self.nav.mode == 'mock':
            await self.nav.position(PositionInput(
                **MOCK_START.model_dump(), source='mock', accuracy_m=3, captured_at=utc_now(),
            ))

    def candidate_prompt(self):
        choices = self.nav.candidates[self.offset:self.offset + 3]
        if not choices:
            return '没有找到地点，请说更具体的地名，或填写所在城市。'
        names = '。'.join(
            f'第{i}个，{poi.label[:32]}，{poi.address[:24]}'
            if poi.address and poi.source != 'mock' else f'第{i}个，{poi.label[:32]}'
            for i, poi in enumerate(choices, 1)
        )
        return names + '。请说第几个，或说下一组。'

    async def execute(self, request, owner):
        replies = []
        for action in plan_actions(request.text):
            self.check(owner)
            try:
                if action.tool == 'navigation':
                    reply = await self.navigate(action.text, request, owner)
                elif action.tool == 'vision':
                    reply = await self.answer(request.model_copy(update={'text': action.text}))
                elif action.tool == 'distance':
                    reply = self.distance_prompt()
                elif action.tool == 'device':
                    reply = await self.device_prompt()
                elif action.tool == 'route_status':
                    reply = self.route_prompt()
                else:
                    reply = action.text if action.text.startswith(('请一次说', '请先听完')) else HELP
            except (NavError, NotConfigured, VisionError) as exc:
                reply = str(exc)
            except asyncio.TimeoutError:
                reply = '这一项响应超时，请稍后重试。'
            if reply:
                replies.append(reply)
            if not self.owner:
                break
        return '。'.join(reply.rstrip('。') for reply in replies) + '。' if replies else ''

    def distance_prompt(self):
        reading = self.service.rules.evaluate(self.service.distance_sample)
        prefix = '模拟测距。' if reading['simulated'] else ''
        if reading['status'] != 'valid':
            return prefix + reading['message']
        distance = reading['distance_m']
        value = f'{round(distance * 100)}厘米' if distance < 1 else f'{distance:.1f}米'
        return f"{prefix}前方声纳读数约{value}。{reading['message']}"

    async def device_prompt(self):
        state = await self.service.state()
        connected = state['telemetry']['status']
        camera = {'valid': '画面可用', 'stale': '画面已过期'}.get(
            state['camera']['status'], '暂无可用画面')
        imu = '姿态可用' if state['imu']['status'] == 'valid' else '暂无有效姿态'
        label = {'connected': '眼镜遥测已连接', 'connecting': '眼镜遥测正在连接',
                 'simulated': '当前使用模拟遥测'}.get(connected, '眼镜遥测暂不可用')
        return f'{label}。相机{camera}。IMU{imu}。'

    def route_prompt(self):
        if self.pending_candidates():
            return '正在选择目的地。' + self.candidate_prompt()
        if not self.owns_route():
            return '助手当前没有路线。可以说带我去某个地点。'
        nav = self.service.navigation_state()
        if self.nav.status == 'preview':
            return f"已规划到{self.nav.route.destination.label[:60]}，请说确认出发。"
        return nav['message']

    async def navigate(self, text, request, owner):
        if text in ('结束助手', '退出助手', '关闭助手'):
            self.end()
            return '语音助手已结束。'
        if text in ('取消', '结束导航', '取消导航', '停止导航'):
            if self.owns_route() or self.pending_candidates():
                self.nav.stop()
            self.search_id = self.route_id = None
            return '已取消。'
        if text in ('帮助', '你能做什么'):
            return HELP
        if text in ('确认出发', '确认开始导航'):
            if not self.owns_route() or self.nav.status != 'preview':
                return '请先选择地点，听完路线摘要再确认出发。'
            if self.nav.mode == 'amap' and not request.allow_location:
                return '请先允许本次会话使用手机位置。'
            await self.reset_mock_position()
            self.nav.start()
            return '导航已开始。'
        if text in ('暂停导航', '暂停'):
            if not self.owns_route() or not self.nav.active:
                return '当前没有正在进行的导航。'
            self.nav.pause()
            return '导航已暂停。说继续导航可以恢复。'
        if text in ('继续导航', '恢复导航'):
            if not self.owns_route() or not self.nav.active:
                return '当前没有可恢复的导航。'
            if self.nav.mode == 'amap' and not request.allow_location:
                return '请先允许本次会话使用手机位置。'
            self.nav.start()
            return '已恢复导航。'
        if text in ('重复上一条', '再说一遍', '重复导航'):
            if self.pending_candidates():
                return self.candidate_prompt()
            if self.owns_route():
                # The browser reads the current instruction from the guarded speech endpoint.
                nav = self.service.navigation_state()
                if self.nav.status != 'preview':
                    return '' if nav['instruction'] else nav['message']
            return self.last_reply or HELP
        if text in ('下一组', '下一页', '更多候选'):
            if not self.pending_candidates():
                return '请先说要去的地点。'
            if self.nav.mode == 'amap' and not request.allow_location:
                return '请先允许本次会话使用高德地图。'
            if self.offset + 3 < len(self.nav.candidates):
                self.offset += 3
            elif self.nav.mode == 'amap' and self.page < 5 and self.nav.candidates:
                await self.search(self.query, self.region, self.page + 1, owner)
            else:
                return '没有更多候选，请换一个地点名称。'
            return self.candidate_prompt()
        selection = NUMBER.fullmatch(text)
        if selection:
            if not self.pending_candidates():
                return '请先说要去的地点，再选择候选。'
            index = ORDINALS.get(selection[1], selection[1])
            index = int(index) - 1
            choices = self.nav.candidates[self.offset:self.offset + 3]
            if not 0 <= index < len(choices):
                return '该编号没有候选。' + self.candidate_prompt()
            if self.nav.mode == 'amap' and not request.allow_location:
                return '请先允许本次会话使用手机位置进行路线规划。'
            poi = choices[index]
            await self.reset_mock_position()
            await self.nav.plan(self.search_id, poi.candidate_id)
            self.check(owner)
            self.route_id = self.nav.route.route_id
            prefix = '模拟路线。' if self.nav.mode == 'mock' else ''
            return f'{prefix}前往{poi.label[:60]}，步行约{self.nav.route.distance_m:.0f}米。请说确认出发，或说取消。'
        destination = DESTINATION.fullmatch(text)
        if destination:
            query = destination[1]
            if len(query) > 80:
                return '请说一个简短的目的地名称。'
            if self.nav.mode == 'amap' and not request.allow_location:
                return '请先允许本次会话使用高德地图。'
            await self.search(query, request.region.strip(), 1, owner)
            return self.candidate_prompt()
        return HELP

    async def search(self, destination, region, page, owner):
        self.search_id = self.route_id = None
        await self.nav.search(destination, region, page)
        self.check(owner)
        self.search_id = self.nav.search_id
        self.query, self.region, self.page, self.offset = destination, region, page, 0

    async def answer(self, request):
        service = self.service
        frame = await service.camera.snapshot()
        if not frame.valid or (not frame.static and time.monotonic() - frame.received_mono > service.config.frame_ttl_s):
            raise NavError('实时画面已过期，等相机恢复后再试。')
        provider = service.vision
        status = vision_status(service.config, service.vision_name)
        if status['external_upload_enabled'] and not request.allow_frame:
            raise NavError('请在助手页面允许本次会话发送问答图片。')
        answer = await asyncio.wait_for(provider.answer(frame, request.text), service.config.model_timeout_s)
        if not isinstance(answer, str) or not answer.strip():
            raise NavError('视觉模型没有返回有效答案。')
        return answer.strip()[:500]


def assistant_router(service):
    router = APIRouter(prefix='/api/assistant')
    agent = Conversation(service)

    def authenticate(request):
        owner = request.headers.get('X-Assistant-Token', '')
        agent.check(owner)
        return owner

    @router.post('/session')
    async def session(request: Request):
        token = request.headers.get('X-Navigation-Token', '')
        if not token.isascii() or not secrets.compare_digest(token, agent.nav.token):
            raise HTTPException(403, '请刷新助手页面')
        if agent.turn_lock.locked():
            raise HTTPException(409, '上一条指令正在处理')
        return {'token': agent.begin()}

    @router.delete('/session')
    async def end(request: Request):
        authenticate(request)
        agent.end()
        return {'ended': True}

    @router.get('/state')
    async def state(request: Request):
        authenticate(request)
        nav = service.navigation_state()
        return {'navigation': {'status': nav['status'], 'message': nav['message'],
                               'instruction': nav['instruction'] if agent.owns_route() and nav['speech_enabled'] else None,
                               'route_id': agent.route_id, 'step_index': nav['step_index']},
                'mode': agent.nav.mode}

    async def finish(turn, owner):
        try:
            reply = await agent.execute(turn, owner)
        except (NavError, NotConfigured, VisionError) as exc:
            reply = str(exc)
        except asyncio.TimeoutError:
            reply = '视觉服务响应超时，请重试。'
        ended = not agent.owner
        if not ended:
            agent.check(owner)
            if reply:
                agent.last_reply = reply
        audio_id = None
        agent.clip = None
        if reply and not ended and service.speech.tts_enabled and not service.device.recordings.current:
            try:
                audio = await service.speech.synthesize(reply[:500])
                agent.check(owner)
                audio_id = secrets.token_urlsafe(12)
                agent.clip = (audio_id, audio)
            except SpeechError:
                pass
        repeat_route = not ended and agent.owns_route() and any(
            action.tool == 'route_status'
            or (action.tool == 'navigation' and action.text in ('重复上一条', '再说一遍', '重复导航'))
            for action in plan_actions(turn.text)
        )
        return {'heard': turn.text, 'reply': reply, 'audio_id': audio_id,
                'ended': ended, 'repeat_route': repeat_route}

    @router.post('/turn')
    async def turn(body: Turn, request: Request):
        owner = authenticate(request)
        if agent.turn_lock.locked():
            raise HTTPException(429, '上一条指令正在处理')
        async with agent.turn_lock:
            return await finish(body, owner)

    @router.post('/voice')
    async def voice(request: Request):
        owner = authenticate(request)
        if service.config.asr_provider != 'vosk':
            raise HTTPException(409, '本机 Vosk 语音识别未启用')
        if request.headers.get('content-type') != 'application/octet-stream':
            raise HTTPException(415, '需要 16 千赫兹 PCM 录音')
        if agent.turn_lock.locked():
            raise HTTPException(429, '上一条指令正在处理')
        async with agent.turn_lock:
            pcm = bytearray()

            async def receive():
                async for chunk in request.stream():
                    if len(pcm) + len(chunk) > 320000:
                        raise HTTPException(413, '每条指令最长十秒')
                    pcm.extend(chunk)

            try:
                await asyncio.wait_for(receive(), 8)
                if len(pcm) < 6400 or len(pcm) % 2:
                    raise HTTPException(422, '录音太短或不完整')
                heard = await service.speech.transcribe(Recording(0, bytes(pcm), 16000, time.monotonic()))
            except SpeechError as exc:
                raise HTTPException(409, str(exc)) from None
            except asyncio.TimeoutError:
                raise HTTPException(408, '录音上传超时') from None
            agent.check(owner)
            return await finish(Turn(
                text=heard, region=request.query_params.get('region', '')[:40],
                allow_frame=request.query_params.get('allow_frame') == '1',
                allow_location=request.query_params.get('allow_location') == '1',
            ), owner)

    @router.get('/audio/{clip_id}')
    async def audio(clip_id: str, request: Request):
        authenticate(request)
        if not agent.clip or agent.clip[0] != clip_id or service.device.recordings.current:
            raise HTTPException(404, '当前没有可播放的回复')
        return Response(agent.clip[1], media_type='audio/wav')

    return router
