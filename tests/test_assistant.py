import httpx
import pytest

from app.config import Settings
from app.main import create_app


async def begin(client):
    token = (await client.get('/api/navigation/session')).json()['token']
    session = await client.post('/api/assistant/session', headers={'X-Navigation-Token': token})
    return {'X-Assistant-Token': session.json()['token']}


async def test_vision_and_confirmed_voice_navigation():
    app = create_app(Settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)

        async def turn(text):
            response = await client.post('/api/assistant/turn', headers=headers, json={'text': text})
            assert response.status_code == 200
            return response.json()['reply']

        assert '画面右侧' in await turn('杯子在哪里？')
        assert '请先选择' in await turn('确认出发')
        assert '第1个' in await turn('步行去图书馆')
        assert '请说确认出发' in await turn('第二个')
        nav = app.state.service.navigation
        assert nav.status == 'preview' and not nav.active
        assert '导航已开始' in await turn('确认出发')
        assert nav.active and nav.state()['instruction']
        assert '已暂停' in await turn('暂停导航')
        assert nav.state()['instruction'] is None
        assert '已恢复' in await turn('继续导航')
        assert '已取消' in await turn('取消')
        assert nav.route is None


async def test_assistant_audio_uses_local_transcript_and_requires_session(monkeypatch):
    app = create_app(Settings(asr_provider='vosk', tts_provider='espeak'))
    service = app.state.service

    async def transcribe(recording):
        assert recording.sample_rate == 16000
        return '步行去图书馆'

    async def synthesize(text):
        assert '图书馆' in text
        return b'local-test-audio'

    monkeypatch.setattr(service.speech, 'transcribe', transcribe)
    monkeypatch.setattr(service.speech, 'synthesize', synthesize)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)
        headers['Content-Type'] = 'application/octet-stream'
        response = await client.post('/api/assistant/voice', headers=headers, content=b'\0' * 6400)
        assert response.status_code == 200
        clip = '/api/assistant/audio/' + response.json()['audio_id']
        assert (await client.get(clip)).status_code == 403
        assert (await client.get(clip, headers=headers)).content == b'local-test-audio'
        assert (await client.delete('/api/assistant/session', headers=headers)).status_code == 200
        assert (await client.get(clip, headers=headers)).status_code == 403


@pytest.mark.parametrize('utterance', ['步行去图书馆', '杯子在哪里'])
async def test_external_tools_require_session_permission(utterance):
    config = Settings(map_provider='amap', allow_external_maps=True,
                      vision_provider='openai_compatible', vision_api_base_url='https://example.test',
                      vision_api_model='vision', allow_external_vision=True)
    app = create_app(config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)
        result = await client.post('/api/assistant/turn', headers=headers, json={'text': utterance})
        assert '请先允许' in result.json()['reply'] or '请在助手页面允许' in result.json()['reply']


async def test_agent_dispatches_each_intent_in_order_and_respects_confirmation(monkeypatch):
    app = create_app(Settings())
    service = app.state.service
    seen = []

    async def answer(frame, question):
        seen.append(question)
        return '杯子在画面右侧。'

    monkeypatch.setattr(service.vision, 'answer', answer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)

        async def turn(text):
            result = await client.post('/api/assistant/turn', headers=headers, json={'text': text})
            assert result.status_code == 200
            return result.json()['reply']

        await client.post('/api/distance', json={'status': 'valid', 'distance_m': .3})
        reply = await turn('先告诉我障碍有多远，再看看杯子在哪里，然后带我去图书馆')
        assert reply.index('30厘米') < reply.index('画面右侧') < reply.index('第1个')
        assert len(seen) == 1 and '障碍' not in seen[0]
        assert '请先听完' in await turn('第二个，然后确认出发')
        assert service.navigation.route is None
        assert '请说确认出发' in await turn('第二个')
        assert '导航已开始' in await turn('确认出发')
        assert '障碍提醒优先' in await turn('现在怎么走')


async def test_unknown_and_device_queries_do_not_call_vision(monkeypatch):
    app = create_app(Settings())

    async def unexpected_call(*args):
        pytest.fail('This request must not reach the vision provider')

    monkeypatch.setattr(app.state.service.vision, 'answer', unexpected_call)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)
        for text, expected in [('几点了', '可以问'), ('摄像头在线吗', '画面可用')]:
            result = await client.post('/api/assistant/turn', headers=headers, json={'text': text})
            assert expected in result.json()['reply']


async def test_destination_is_filled_and_arrival_returns_to_conversation(monkeypatch):
    from app.maps import MOCK_CORNER, MOCK_END
    from app.nav_types import PositionInput
    from app.domain import utc_now

    app = create_app(Settings(tts_provider='espeak'))
    service = app.state.service

    async def synthesize(text):
        return b'test-audio'

    monkeypatch.setattr(service.speech, 'synthesize', synthesize)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)

        async def turn(text):
            return (await client.post('/api/assistant/turn', headers=headers, json={'text': text})).json()

        result = await turn('带我去图书馆')
        assert result['destination'] == '图书馆'
        assert not result['amap_available']
        await turn('第二个')
        await turn('确认出发')
        assert '还没有进入' in (await turn('确认到达'))['reply']
        for point in (MOCK_CORNER, MOCK_CORNER.model_copy(update={'latitude': 39.9005}), MOCK_END, MOCK_END):
            await service.navigation.position(PositionInput(
                **point.model_dump(), source='mock', accuracy_m=3, captured_at=utc_now()))
        state = (await client.get('/api/assistant/state', headers=headers)).json()
        assert state['navigation']['near_destination']
        assert (await client.post('/api/assistant/arrival-speech', headers=headers)).content == b'test-audio'
        result = await turn('确认到达')
        assert '已结束导航' in result['reply'] and not result['ended']
        assert service.navigation.route is None
        assert '画面右侧' in (await turn('杯子在哪里'))['reply']


async def test_campus_access_protects_pages_assets_and_apis():
    password = 'test-password-0123456789'
    app = create_app(Settings(web_access_password=password))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
        for path in ('/assistant', '/api/state', '/api/navigation/session', '/static/assistant.js'):
            response = await client.get(path)
            assert response.status_code == 401
            assert response.headers['www-authenticate'].startswith('Basic')
            assert (await client.get(path, auth=('3800', 'incorrect'))).status_code == 401
            assert (await client.get(path, auth=('3800', password))).status_code == 200


async def test_voice_replay_preserves_candidate_route_and_progress(monkeypatch):
    from app.speech import SpeechError

    app = create_app(Settings(asr_provider='vosk'))
    service = app.state.service
    heard = iter(('带我去图书馆', None, '听不清的一句', '我选第２个吧', '去餐厅',
                  '保留原路线', '确认出发吧', '去不存在的地方', '确认换目的地',
                  '取消', '确认结束'))

    async def transcribe(recording):
        text = next(heard)
        if text is None:
            raise SpeechError('未听清问题')
        return text

    monkeypatch.setattr(service.speech, 'transcribe', transcribe)
    candidates = service.navigation.provider.candidates

    async def search(query, region):
        return [] if query == '不存在的地方' else await candidates(query, region)

    monkeypatch.setattr(service.navigation.provider, 'candidates', search)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)

        async def say():
            response = await client.post('/api/assistant/voice',
                                         headers={**headers, 'Content-Type': 'application/octet-stream'},
                                         content=b'\0' * 6400)
            assert response.status_code == 200
            return response.json()['reply']

        nav = service.navigation
        assert '第1个' in await say()
        original_search = nav.search_id
        assert '未听清' in await say()
        assert '正在选择目的地' in await say()
        assert nav.search_id == original_search
        assert '请说确认出发' in await say()
        original_route = nav.route
        assert '确认换目的地' in await say()
        assert nav.route is original_route
        assert '已保留当前进度' in await say()
        assert '导航已开始' in await say()
        assert '当前进度已保留' in await say()
        assert '没有找到' in await say()
        assert nav.route is original_route and nav.active
        assert '确认结束' in await say()
        assert nav.route is original_route and nav.active
        assert '已取消' in await say()
        assert not nav.route


async def test_amap_voice_handoff_uses_selected_poi_without_browser_gps(monkeypatch):
    from app.maps import MOCK_END
    from app.nav_types import Candidate

    app = create_app(Settings(map_provider='amap', allow_external_maps=True))
    nav = app.state.service.navigation
    poi = Candidate(candidate_id='amap-1', label='校区图书馆', point=MOCK_END, source='amap')

    async def candidates(query, region, *, page=1):
        assert query == '图书馆'
        return [poi]

    monkeypatch.setattr(nav.provider, 'candidates', candidates)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)

        async def turn(text, allowed=True):
            return (await client.post('/api/assistant/turn', headers=headers,
                                     json={'text': text, 'allow_location': allowed})).json()

        assert not (await turn('用高德导航去图书馆', False))['handoff']
        assert '校区图书馆' in (await turn('用高德地图步行导航到图书馆'))['reply']
        assert not (await turn('选第一个吧'))['handoff']
        assert '已选校区图书馆' in (await turn('再说一遍'))['reply']
        assert not (await turn('确认出发', False))['handoff']
        assert (await turn('确认出发'))['handoff'] == {
            'kind': 'walk', 'label': poi.label,
            'latitude': poi.point.latitude, 'longitude': poi.point.longitude,
        }
        assert nav.location is None and nav.route is None
        assert (await turn('打开高德搜索图书馆'))['handoff']['kind'] == 'search'


async def test_replace_search_timeout_keeps_current_candidates(monkeypatch):
    import asyncio

    app = create_app(Settings(map_timeout_s=.03))
    nav = app.state.service.navigation
    candidates = nav.provider.candidates

    async def search(query, region):
        if query == '食堂':
            await asyncio.sleep(1)
        return await candidates(query, region)

    monkeypatch.setattr(nav.provider, 'candidates', search)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await begin(client)

        async def turn(text):
            return (await client.post('/api/assistant/turn', headers=headers, json={'text': text})).json()['reply']

        assert '第1个' in await turn('带我去图书馆')
        search_id = nav.search_id
        assert '当前进度已保留' in await turn('带我去食堂')
        assert '超时' in await turn('确认换目的地')
        assert nav.search_id == search_id and nav.status == 'select_destination'
        assert '请说确认出发' in await turn('第一个')
