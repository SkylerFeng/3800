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
