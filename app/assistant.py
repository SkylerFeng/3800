"""Conversation and tool calls for the foreground 3800 voice assistant."""
import asyncio
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from PIL import Image

from .assistant_intents import AMAP, AMAP_SEARCH, DESTINATION, NUMBER, plan_actions
from .device import Recording
from .domain import utc_now
from .maps import MOCK_START
from .nav_types import NavError, PositionInput
from .providers import LatestCamera, NotConfigured
from .speech import SpeechError
from .vision import VisionError, vision_status

HELP = '可以问前方障碍有多远、杯子在哪里，或说步行去图书馆、用高德导航去图书馆。'
ORDINALS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5}
STOP_COMMANDS = frozenset(('结束助手', '退出助手', '关闭助手', '取消', '结束导航', '取消导航', '停止导航'))


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
        self.destination_draft = ''
        self.arrival_clip = None
        self.replacement = None
        self.retry_search = None
        self.external_search = False
        self.external_choice = None
        self.handoff = None
        self.pending_stop = None
        self.pending_snapshot = None

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
        self.destination_draft = ''
        self.arrival_clip = None
        self.replacement = self.retry_search = self.external_choice = self.handoff = None
        self.external_search = False
        self.pending_stop = None
        self.pending_snapshot = None
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

    def recovery_prompt(self):
        if self.pending_stop and time.monotonic() < self.pending_stop[1]:
            return '正在等待结束确认。可以说确认结束，或继续当前操作。'
        if self.replacement:
            return f'正在等你确认是否改去{self.replacement[0]}。请说确认换目的地，或说保留原路线。'
        if self.external_choice:
            return f'已选{self.external_choice.label[:60]}。请说确认出发，打开高德步行导航。'
        if self.retry_search:
            reminder = '可说重试搜索，或重新说目的地。'
            if self.pending_candidates():
                return reminder + '原候选仍在，可说第几个或再说一遍。'
            if self.owns_route():
                return reminder + '原路线仍在，可问导航进度。'
            return reminder
        if self.pending_candidates():
            return '正在选择目的地。可说第几个，或说再说一遍听候选。'
        if self.owns_route():
            return self.route_prompt()
        return HELP

    async def execute(self, request, owner, *, voice=False, phone=False, frame=None):
        replies = []
        self.destination_draft = ''
        self.handoff = None
        actions = plan_actions(request.text)
        for action in actions:
            self.check(owner)
            try:
                if action.tool == 'navigation':
                    reply = await self.navigate(action.text, request, owner, voice=voice)
                elif action.tool == 'amap':
                    reply = await self.open_amap(action.text, request, owner)
                elif action.tool == 'vision':
                    if phone and frame is None:
                        if not request.allow_frame and vision_status(
                            self.service.config, self.service.vision_name,
                        )['external_upload_enabled']:
                            raise NavError('请在权限与设置允许发送问答画面。')
                        raise NavError('手机未能从眼镜取图。请检查眼镜热点与 IP。')
                    reply = await self.answer(request.model_copy(update={'text': action.text}), frame)
                elif action.tool == 'distance':
                    reply = '随身模式尚未接入眼镜声纳。' if phone else self.distance_prompt()
                elif action.tool == 'device':
                    reply = ('请在随身助手的连接设置测试眼镜取图。IMU 与声纳尚未转发。'
                             if phone else await self.device_prompt())
                elif action.tool == 'route_status':
                    reply = self.route_prompt()
                else:
                    reply = (action.text if action.text.startswith(('请一次说', '请先听完'))
                             else '这句话没听明白。' + self.recovery_prompt())
            except (NavError, NotConfigured, VisionError) as exc:
                reply = str(exc)
                if action.tool in ('navigation', 'amap') and (
                    self.pending_candidates() or self.owns_route() or self.retry_search
                ):
                    reply += '。' + self.recovery_prompt()
            except asyncio.TimeoutError:
                reply = '这一项响应超时，请稍后重试。'
            if reply:
                replies.append(reply)
            if not self.owner or self.handoff:
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

    def handoff_walk(self, poi):
        if poi.source != 'amap' or poi.point.coordinate_system != 'GCJ02':
            return '这个地点没有可用于高德的坐标。'
        self.handoff = {'kind': 'walk', 'label': poi.label,
                        'longitude': poi.point.longitude, 'latitude': poi.point.latitude}
        return f'正在尝试打开高德，目的地是{poi.label[:60]}。如果没有打开，请点打开高德地图。'

    async def open_amap(self, text, request, owner):
        if self.nav.mode != 'amap' or not self.service.config.allow_external_maps:
            return '当前没有启用真实高德地图。'
        if not request.allow_location:
            return '请先允许本次会话使用高德地图。'
        keyword = AMAP_SEARCH.fullmatch(text)
        if keyword:
            self.destination_draft = keyword[1][:80]
            self.handoff = {'kind': 'search', 'label': self.destination_draft, 'region': request.region}
            return f'正在尝试打开高德搜索{self.destination_draft}。如果没有打开，请点打开高德地图。'
        suffix = AMAP.fullmatch(text)[1]
        if suffix:
            destination = DESTINATION.fullmatch(suffix)
            query = destination[1] if destination else re.sub(r'^[到去]', '', suffix)
            return await self.request_search(query, request, owner, external=True)
        if self.external_choice:
            return self.handoff_walk(self.external_choice)
        if self.owns_route():
            return self.handoff_walk(self.nav.route.destination)
        if self.pending_candidates():
            self.external_search = True
            return '将用高德步行导航。' + self.candidate_prompt()
        return '请说用高德导航去某个地点，或说打开高德搜索加地点。'

    async def request_search(self, query, request, owner, *, external=False):
        if not query or len(query) > 80:
            return '请说一个简短的目的地名称。'
        self.destination_draft = query
        if self.nav.mode == 'amap' and not request.allow_location:
            return '请先允许本次会话使用高德地图。'
        target = (query, request.region.strip(), external)
        if self.owns_route() or self.pending_candidates():
            if query != self.query or (target[1] and target[1] != self.region):
                self.replacement = target
                return f'听到新目的地{query}。当前进度已保留。请说确认换目的地，或说保留原路线。'
            if external:
                if self.owns_route():
                    return self.handoff_walk(self.nav.route.destination)
                self.external_search = True
            return self.recovery_prompt()
        return await self.search_destination(target, owner)

    async def search_destination(self, target, owner):
        query, region, external = target
        previous = self.nav.search_id
        self.retry_search = target
        self.replacement = None
        await self.search(query, region, 1, owner)
        if previous == self.nav.search_id:
            return f'没有找到{query}。' + self.recovery_prompt()
        self.retry_search = self.replacement = self.external_choice = None
        self.external_search = external
        return self.candidate_prompt()

    async def navigate(self, text, request, owner, *, voice=False):
        pending, self.pending_stop = self.pending_stop, None
        if text == '确认结束':
            if not pending or time.monotonic() >= pending[1]:
                return '当前没有等待确认的结束操作。' + self.recovery_prompt()
            text = pending[0]
        elif (voice and text in STOP_COMMANDS and not (text == '取消' and self.replacement)
              and (self.owns_route() or self.pending_candidates() or self.external_choice)):
            self.pending_stop = (text, time.monotonic() + 30)
            return '听到结束指令，当前进度已保留。要结束请说确认结束。'
        if text in ('确认换目的地', '重试搜索'):
            target = self.replacement if text == '确认换目的地' else self.retry_search
            if not target:
                return self.recovery_prompt()
            if self.nav.mode == 'amap' and not request.allow_location:
                return '请先允许本次会话使用高德地图。'
            return await self.search_destination(target, owner)
        if text in ('保留原路线', '取消换目的地') or (text == '取消' and self.replacement):
            self.replacement = self.retry_search = None
            return '已保留当前进度。' + self.recovery_prompt()
        if text in ('确认到达', '我到了'):
            if not self.owns_route() or self.nav.state()['status'] != 'near_destination':
                return '还没有进入目的地附近。你也可以说结束导航。'
            self.nav.stop()
            self.route_id = self.search_id = None
            self.replacement = self.retry_search = None
            return '已结束导航。你还可以继续问眼镜，或告诉我下一个目的地。'
        if text in ('结束助手', '退出助手', '关闭助手'):
            self.end()
            return '语音助手已结束。'
        if text in ('取消', '结束导航', '取消导航', '停止导航'):
            if self.owns_route() or self.pending_candidates():
                self.nav.stop()
            self.search_id = self.route_id = None
            self.external_choice = self.retry_search = self.replacement = None
            self.external_search = False
            return '已取消。'
        if text in ('帮助', '你能做什么'):
            return HELP
        if text in ('确认出发', '确认开始导航'):
            if self.replacement:
                return self.recovery_prompt()
            if self.external_choice:
                if not request.allow_location:
                    return '请先允许本次会话使用高德地图。'
                return self.handoff_walk(self.external_choice)
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
            if self.replacement or self.external_choice:
                return self.recovery_prompt()
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
                previous = self.search_id
                await self.search(self.query, self.region, self.page + 1, owner)
                if previous == self.search_id:
                    return '没有更多候选。' + self.candidate_prompt()
            else:
                return '没有更多候选，请换一个地点名称。'
            self.external_choice = None
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
            self.replacement = self.retry_search = None
            if self.external_search:
                self.external_choice = poi
                return self.recovery_prompt()
            await self.reset_mock_position()
            await self.nav.plan(self.search_id, poi.candidate_id)
            self.check(owner)
            self.route_id = self.nav.route.route_id
            prefix = '模拟路线。' if self.nav.mode == 'mock' else ''
            return f'{prefix}前往{poi.label[:60]}，步行约{self.nav.route.distance_m:.0f}米。请说确认出发，或说取消。'
        destination = DESTINATION.fullmatch(text)
        if destination:
            return await self.request_search(destination[1], request, owner)
        return HELP

    async def search(self, destination, region, page, owner):
        previous = self.nav.search_id
        await self.nav.search(destination, region, page, preserve_on_failure=True)
        self.check(owner)
        if previous == self.nav.search_id:
            return
        self.route_id = None
        self.search_id = self.nav.search_id
        self.query, self.region, self.page, self.offset = destination, region, page, 0

    async def answer(self, request, frame=None):
        service = self.service
        if frame is None:
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
                               'near_destination': agent.owns_route() and nav['status'] == 'near_destination'
                               and not nav['obstacle_priority'] and nav['speech_enabled'],
                               'instruction': nav['instruction'] if agent.owns_route() and nav['speech_enabled'] else None,
                               'route_id': agent.route_id, 'step_index': nav['step_index']},
                'mode': agent.nav.mode}

    @router.post('/arrival-speech')
    async def arrival_speech(request: Request):
        owner = authenticate(request)
        def current():
            nav = service.navigation_state()
            return (agent.owns_route() and nav['status'] == 'near_destination'
                    and not nav['obstacle_priority'] and not nav['recording_active'])
        if not current() or not service.speech.tts_enabled or agent.turn_lock.locked():
            raise HTTPException(409, '当前没有可播报的到达提示')
        async with agent.turn_lock:
            route_id = agent.route_id
            if not agent.arrival_clip or agent.arrival_clip[0] != route_id:
                try:
                    audio = await service.speech.synthesize('已接近目的地。请核对实际入口，到达后说确认到达。')
                except SpeechError as exc:
                    raise HTTPException(409, str(exc)) from None
                agent.check(owner)
                if agent.route_id != route_id or not current():
                    raise HTTPException(409, '路线状态已更新')
                agent.arrival_clip = (route_id, audio)
            return Response(agent.arrival_clip[1], media_type='audio/wav')

    async def finish(turn, owner, *, speech_error=None, voice=False, phone=False, frame=None):
        try:
            reply = (speech_error + '。' + agent.recovery_prompt()
                     if speech_error else await agent.execute(turn, owner, voice=voice, phone=phone, frame=frame))
        except (NavError, NotConfigured, VisionError) as exc:
            reply = str(exc)
        except asyncio.TimeoutError:
            reply = '视觉服务响应超时，请重试。'
        ended = not agent.owner
        if not ended:
            agent.check(owner)
            if reply and not speech_error:
                agent.last_reply = reply
        audio_id = None
        agent.clip = None
        handoff = None if speech_error else agent.handoff
        if handoff:
            agent.expires = time.monotonic() + service.config.assistant_session_s
        if reply and not ended and service.speech.tts_enabled and not service.device.recordings.current:
            try:
                audio = await service.speech.synthesize(reply[:500])
                agent.check(owner)
                audio_id = secrets.token_urlsafe(12)
                agent.clip = (audio_id, audio)
            except SpeechError:
                pass
        repeat_route = not speech_error and not ended and agent.owns_route() and any(
            action.tool == 'route_status'
            or (action.tool == 'navigation' and action.text in ('重复上一条', '再说一遍', '重复导航'))
            for action in plan_actions(turn.text)
        )
        return {'heard': turn.text, 'reply': reply, 'audio_id': audio_id,
                'destination': None if speech_error else agent.destination_draft or None,
                'amap_available': agent.nav.mode == 'amap',
                'handoff': handoff,
                'ended': ended, 'repeat_route': repeat_route}

    async def dispatch(turn, owner, request, *, voice=False):
        phone = request.headers.get('X-3800-Camera') == 'phone'
        if phone and (request.url.scheme != 'https' or not service.config.web_access_password):
            raise HTTPException(403, '随身助手需要在电脑启用 HTTPS 和访问口令')
        if phone and any(action.tool == 'vision' for action in plan_actions(turn.text)):
            vision = vision_status(service.config, service.vision_name)
            if not vision['external_upload_enabled'] or turn.allow_frame:
                ticket = secrets.token_urlsafe(18)
                agent.pending_snapshot = (ticket, time.monotonic() + 20, turn, voice)
                return {'snapshot_id': ticket}
        return await finish(turn, owner, voice=voice, phone=phone)

    @router.post('/snapshot/{ticket}')
    async def phone_snapshot(ticket: str, request: Request):
        owner = authenticate(request)
        if request.url.scheme != 'https' or not service.config.web_access_password:
            raise HTTPException(403, '随身助手需要在电脑启用 HTTPS 和访问口令')
        if agent.turn_lock.locked():
            raise HTTPException(429, '上一条指令正在处理')
        async with agent.turn_lock:
            pending = agent.pending_snapshot
            if not pending or ticket != pending[0] or time.monotonic() >= pending[1]:
                raise HTTPException(409, '取图请求已过期，请重新提问')
            agent.pending_snapshot = None
            if request.headers.get('content-type') != 'image/jpeg':
                raise HTTPException(415, '需要 JPEG 图像')
            image = bytearray()

            async def receive():
                async for chunk in request.stream():
                    if len(image) + len(chunk) > min(1024 * 1024, service.config.upload_max_bytes):
                        raise HTTPException(413, '眼镜画面超过大小限制')
                    image.extend(chunk)

            try:
                await asyncio.wait_for(receive(), 8)
            except asyncio.TimeoutError:
                raise HTTPException(408, '上传眼镜画面超时') from None
            frame = None
            if image:
                try:
                    if not image.startswith(b'\xff\xd8'):
                        raise ValueError()
                    frame = await asyncio.to_thread(
                        LatestCamera().set_image, bytes(image), 'phone_snapshot', False,
                    )
                except (OSError, ValueError, Image.DecompressionBombError):
                    raise HTTPException(422, '眼镜画面无法解码') from None
            agent.check(owner)
            return await finish(pending[2], owner, voice=pending[3], phone=True, frame=frame)

    @router.post('/turn')
    async def turn(body: Turn, request: Request):
        owner = authenticate(request)
        if agent.turn_lock.locked():
            raise HTTPException(429, '上一条指令正在处理')
        async with agent.turn_lock:
            agent.pending_snapshot = None
            return await dispatch(body, owner, request)

    @router.post('/voice')
    async def voice(request: Request):
        owner = authenticate(request)
        if not service.speech.asr_enabled:
            raise HTTPException(409, '本地语音识别未启用')
        if request.headers.get('content-type') != 'application/octet-stream':
            raise HTTPException(415, '需要 16 千赫兹 PCM 录音')
        if agent.turn_lock.locked():
            raise HTTPException(429, '上一条指令正在处理')
        async with agent.turn_lock:
            agent.pending_snapshot = None
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
                agent.check(owner)
                return await finish(Turn(text=' '), owner, speech_error=str(exc))
            except asyncio.TimeoutError:
                raise HTTPException(408, '录音上传超时') from None
            agent.check(owner)
            return await dispatch(Turn(
                text=heard, region=request.query_params.get('region', '')[:40],
                allow_frame=request.query_params.get('allow_frame') == '1',
                allow_location=request.query_params.get('allow_location') == '1',
            ), owner, request, voice=True)

    @router.get('/audio/{clip_id}')
    async def audio(clip_id: str, request: Request):
        authenticate(request)
        if not agent.clip or agent.clip[0] != clip_id or service.device.recordings.current:
            raise HTTPException(404, '当前没有可播放的回复')
        return Response(agent.clip[1], media_type='audio/wav')

    return router
