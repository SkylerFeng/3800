import io

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.main import create_app

PASSWORD = 'mobile-test-password'


def request_body(payload):
    output = io.BytesIO()
    scene = Image.new('RGB', (32, 24), 'blue')
    scene.paste('red', (0, 0, 16, 24))
    scene.save(output, 'JPEG')
    jpeg = output.getvalue()
    return len(jpeg).to_bytes(4, 'big') + jpeg + payload


async def test_android_login_cookie_requires_https_and_basic_auth():
    app = create_app(Settings(web_access_password=PASSWORD))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
        headers = {'X-3800-WebView': '1'}
        assert (await client.get('/api/mobile/status', headers=headers)).status_code == 401
        response = await client.get('/api/mobile/status', headers=headers, auth=('3800', PASSWORD))
        assert response.status_code == 200
        assert all(flag in response.headers['set-cookie'] for flag in ('Secure', 'HttpOnly', 'SameSite=strict'))
        assert (await client.get('/api/navigation/session')).status_code == 200
        response = await client.get('http://test/api/navigation/session',
                                    headers={'Cookie': '__Host-3800-mobile=' + response.cookies['__Host-3800-mobile']})
        assert response.status_code == 401


async def test_phone_snapshot_is_request_local_and_requires_https_and_password():
    app = create_app(Settings(web_access_password=PASSWORD))
    service = app.state.service
    original = service.camera.frame
    data = request_body('杯子在哪'.encode())
    headers = {'Content-Type': 'application/octet-stream'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
        assert (await client.get('/api/mobile/status')).status_code == 401
        client.auth = ('3800', PASSWORD)
        assert (await client.get('http://test/api/mobile/status')).status_code == 403
        assert (await client.get('/api/mobile/status')).json()['camera_input'] == 'phone_snapshot'
        response = await client.post('/api/mobile/ask', headers=headers, content=data)
        assert response.status_code == 200
        assert response.json()['heard'] == '杯子在哪'
        assert '模拟回答' in response.json()['reply']
        assert response.json()['frame_id'] != original.frame_id
        assert service.camera.frame is original and service.camera.mode == 'mock'
        assert (await client.post('/api/mobile/ask', headers=headers, content=data[:30])).status_code == 422


async def test_phone_voice_obeys_cloud_permission_and_returns_answer_audio(monkeypatch):
    app = create_app(Settings(web_access_password=PASSWORD, asr_provider='sensevoice',
                              tts_provider='matcha', vision_provider='openai_compatible',
                              allow_external_vision=True,
                              vision_api_base_url='https://mock.example', vision_api_model='test'))
    service = app.state.service
    seen = []

    async def transcribe(recording):
        assert recording.sample_rate == 16000 and len(recording.pcm) == 8000
        return '前面有什么'

    async def answer(frame, question):
        assert frame.source == 'phone_snapshot' and frame.width == 32 and frame.static
        with Image.open(io.BytesIO(frame.content)) as image:
            assert image.getpixel((4, 12))[2] > 220
            assert image.getpixel((28, 12))[0] > 220
        seen.append(question)
        return '画面中央有一个杯子。'

    async def speak(reply):
        assert '杯子' in reply
        return b'RIFF'

    monkeypatch.setattr(service.speech, 'transcribe', transcribe)
    monkeypatch.setattr(service.speech, 'synthesize', speak)
    monkeypatch.setattr(service.vision, 'answer', answer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test',
                                 auth=('3800', PASSWORD)) as client:
        headers = {'Content-Type': 'application/octet-stream'}
        url = '/api/mobile/ask?input=voice'
        data = request_body(bytes(8000))
        assert (await client.post(url, headers=headers, content=data)).status_code == 409
        assert seen == []
        response = await client.post(url + '&allow_cloud=true', headers=headers, content=data)
        assert response.status_code == 200
        assert seen == ['前面有什么']
        assert response.json()['audio_wav'] == 'UklGRg=='


@pytest.mark.parametrize('data', [b'\0\0\0\x04\xff\xd8\xff\xd9a', b'\0' * (1024 * 1024 + 2005)])
async def test_mobile_rejects_invalid_or_oversized_data(data):
    app = create_app(Settings(web_access_password=PASSWORD))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test',
                                 auth=('3800', PASSWORD)) as client:
        response = await client.post('/api/mobile/ask', content=data,
                                     headers={'Content-Type': 'application/octet-stream'})
        assert response.status_code == (413 if len(data) > 1024 * 1024 else 422)


async def test_phone_agent_requests_frame_only_for_vision_and_keeps_navigation(monkeypatch):
    app = create_app(Settings(web_access_password=PASSWORD, asr_provider='sensevoice'))
    service = app.state.service
    frames = []

    async def answer(frame, text):
        assert frame.source == 'phone_snapshot'
        with Image.open(io.BytesIO(frame.content)) as image:
            assert image.getpixel((4, 12))[2] > 220
            assert image.getpixel((28, 12))[0] > 220
        assert text in ('杯子在哪里', '杯子在哪')
        frames.append(frame.frame_id)
        return '杯子在中间。'

    async def transcribe(recording):
        return '选第一个吧'

    monkeypatch.setattr(service.vision, 'answer', answer)
    monkeypatch.setattr(service.speech, 'transcribe', transcribe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test',
                                 auth=('3800', PASSWORD)) as client:
        nav = (await client.get('/api/navigation/session')).json()['token']
        session = await client.post('/api/assistant/session', headers={'X-Navigation-Token': nav})
        headers = {'X-Assistant-Token': session.json()['token'], 'X-3800-Camera': 'phone'}

        async def turn(text):
            response = await client.post('/api/assistant/turn', json={'text': text}, headers=headers)
            return response.json()

        assert '第1个' in (await turn('导航到图书馆'))['reply']
        pending = await turn('杯子在哪里')
        url = '/api/assistant/snapshot/' + pending['snapshot_id']
        content = request_body(b'')[4:]
        upload_headers = {**headers, 'Content-Type': 'image/jpeg'}
        response = await client.post(url, content=content, headers=upload_headers)
        assert response.json()['reply'] == '杯子在中间。'
        assert (await client.post(url, content=content, headers=upload_headers)).status_code == 409
        follow_up = await turn('它在哪')
        response = await client.post('/api/assistant/snapshot/' + follow_up['snapshot_id'],
                                     content=content, headers=upload_headers)
        assert response.json()['reply'] == '杯子在中间。'
        assert len(frames) == 2 and frames[0] != frames[1]
        result = await client.post('/api/assistant/voice', content=bytes(8000),
                                   headers={**headers, 'Content-Type': 'application/octet-stream'})
        assert '确认出发' in result.json()['reply']
        assert '已开始' in (await turn('确认出发'))['reply']
        pending = await turn('杯子在哪里，然后问障碍有多远')
        missing = await client.post('/api/assistant/snapshot/' + pending['snapshot_id'], headers=upload_headers)
        assert '手机未能从眼镜取图' in missing.json()['reply']
        assert '尚未接入眼镜声纳' in missing.json()['reply']
