import asyncio
from dataclasses import replace
import io
import json
import time
import wave

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.device import DISTANCE, IMU, MORE, START, END, PCM, STATUS, WireError, XIAO, pack_frame, read_frame
from app.main import create_app
from app.speech import wav_bytes
from app.warning import WarningRules


async def test_wire_reads_split_and_coalesced_frames_and_rejects_crc():
    reader = asyncio.StreamReader()
    first = pack_frame(0x10, IMU.pack(1, 0, 0, 0, 0, 0, 0), 1)
    second = pack_frame(0x11, DISTANCE.pack(300, 300, 1, 0, 0), 2)
    task = asyncio.create_task(read_frame(reader))
    reader.feed_data(first[:7])
    await asyncio.sleep(0)
    assert not task.done()
    reader.feed_data(first[7:] + second)
    assert (await task)[2] == 1
    assert (await read_frame(reader))[2] == 2
    reader.feed_data(second[:-1] + b'\xff')
    with pytest.raises(WireError, match='CRC'):
        await read_frame(reader)
    reader.feed_data(first[:10])
    reader.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)


async def wait_until(check):
    for _ in range(120):
        if check():
            return
        await asyncio.sleep(.01)
    pytest.fail('条件未在限定时间内满足')


async def test_local_tcp_camera_recording_and_haptic_through_app():
    jpeg = io.BytesIO()
    scene = Image.new('RGB', (64, 48), 'blue')
    scene.paste('red', (0, 0, 32, 48))
    scene.save(jpeg, 'JPEG')
    image = jpeg.getvalue()
    wav = wav_bytes(b'\0\0' * 160, 16000)
    closed = asyncio.Event()
    received = []

    async def camera(reader, writer):
        try:
            assert (await reader.readuntil(b'\r\n\r\n')).startswith(b'GET /stream ')
            mime = (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                    str(len(image)).encode() + b'\r\n\r\n' + image + b'\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace;boundary=frame\r\n'
                         b'Transfer-Encoding: chunked\r\n\r\n')
            for part in (mime[:16], mime[16:]):
                writer.write(f'{len(part):x}\r\n'.encode() + part + b'\r\n')
            await writer.drain()
            await closed.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    async def device(reader, writer):
        try:
            writer.write(pack_frame(0x13, STATUS.pack(5000, 12345, -42, 15, 0)))
            writer.write(pack_frame(0x10, IMU.pack(1, 0, 0, 0, 0, 0, 0)))
            writer.write(pack_frame(0x11, DISTANCE.pack(240, 240, 1, 0, 0)))
            writer.write(pack_frame(0x12, PCM.pack(5, 0, 16000, 160, 1, 16) + b'\0\0' * 160,
                                    flags=START | MORE))
            writer.write(pack_frame(0x12, PCM.pack(5, 1, 16000, 0, 1, 16), flags=END))
            await writer.drain()
            kind, _, seq, _, payload = await read_frame(reader)
            received.append(json.loads(payload))
            assert kind == 0x20
            ack = {'rx_sequence': seq, 'status': 'ok', 'motor_ready': True,
                   'motor_fault': False, 'motor_active': True}
            writer.write(pack_frame(0x21, json.dumps(ack).encode()))
            await writer.drain()
            await closed.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    tcp = await asyncio.start_server(device, '127.0.0.1', 0)
    http = await asyncio.start_server(camera, '127.0.0.1', 0)
    async with tcp, http:
        config = Settings(esp32_host='127.0.0.1', camera_source='esp32', telemetry_source='esp32',
                          asr_provider='mock', tts_provider='espeak', haptic_output='esp32',
                          esp32_tcp_port=tcp.sockets[0].getsockname()[1],
                          esp32_camera_port=http.sockets[0].getsockname()[1])
        app = create_app(config)
        service = app.state.service

        async def synthesize(_):
            return wav

        service.speech.synthesize = synthesize
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url='http://test') as client:
                    await wait_until(lambda: service.camera.frame and service.last_haptic)
                    preview = await client.get('/api/frame')
                    with Image.open(io.BytesIO(preview.content)) as frame:
                        assert frame.getpixel((8, 12))[2] > 220
                        assert frame.getpixel((56, 12))[0] > 220
                    state = (await client.get('/api/state')).json()
                    assert state['imu']['quaternion_wxyz'] == [1, 0, 0, 0]
                    assert state['distance']['distance_m'] == .24
                    assert state['distance']['level'] == 'critical'
                    assert state['haptic']['last_event']['result']['status'] == 'confirmed'
                    assert received[0]['name'] == 'motor_pulse'
                    rec = await client.post('/api/recording/transcribe', json={'stream_id': 5})
                    assert rec.json() == {'text': '杯子在哪里？', 'simulated': True}
                    task = await client.post('/api/questions', json={'question': rec.json()['text'], 'frame_id': None})
                    assert task.status_code == 202
                    await wait_until(lambda: service.audio_clip)
                    audio = await client.get('/api/audio/' + task.json()['request_id'])
                    with wave.open(io.BytesIO(audio.content)) as pcm:
                        assert pcm.getnframes() == 160
                    closed.set()
                    await wait_until(lambda: service.device.status == 'disconnected')
                    state = (await client.get('/api/state')).json()
                    assert state['distance']['level'] == 'unknown'
                    assert state['imu']['status'] == 'disconnected'
        finally:
            closed.set()


async def test_pcm_rejects_missing_chunks_and_expires_incomplete():
    from app.device import Recordings
    recordings = Recordings(1)
    recordings.feed(START | MORE, PCM.pack(5, 0, 16000, 1, 1, 16) + b'\0\0')
    recordings.feed(END, PCM.pack(5, 2, 16000, 0, 1, 16))
    assert recordings.latest is None and recordings.current is None
    recordings.feed(START | MORE, PCM.pack(6, 0, 16000, 1, 1, 16) + b'\0\0')
    stream, index, buffer, _ = recordings.current
    recordings.current = stream, index, buffer, time.monotonic() - 8
    recordings.expire(90)
    assert recordings.current is None and recordings.latest is None


@pytest.mark.parametrize('packet,status,level,metres', [
    ((0xffff, 0xfffd, 2, 0, 0), 'no_target', 'unknown', None),
    ((0xffff, 0xfffe, 3, 0, 0), 'interference', 'unknown', None),
    ((3500, 3500, 1, 0, 0), 'out_of_range', 'unknown', None),
    ((18, 18, 1, 0, 0), 'too_close', 'critical', None),
    ((2999, 2999, 1, 0, 0), 'valid', 'normal', 2.999),
])
def test_sonar_distinguishes_range_from_echo_status(packet, status, level, metres):
    config = Settings(sonar_max_m=3)
    device = XIAO(config)
    device.accept(0x11, 0, 1, 0, DISTANCE.pack(*packet))
    rules = WarningRules(config)
    result = rules.evaluate(device.distance)
    assert (result['status'], result['level'], result['distance_m']) == (status, level, metres)
    stale = rules.evaluate(device.distance, now=device.distance.received_mono + 4)
    assert (stale['status'], stale['level'], stale['distance_m']) == ('stale', 'unknown', None)


async def test_preview_waits_for_new_frame_without_using_static_material():
    app = create_app()
    camera = app.state.service.camera
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/camera/live-frame')).status_code == 204
        camera.mode = 'esp32'
        camera.frame = replace(camera.frame, source='esp32', static=False)
        previous = camera.frame.frame_id
        pending = asyncio.create_task(client.get('/api/camera/live-frame', params={'after': previous}))
        await asyncio.sleep(.01)
        assert not pending.done()
        camera.frame = replace(camera.frame, frame_id='new-live-id')
        response = await asyncio.wait_for(pending, 1)
        assert response.headers['X-Frame-ID'] == 'new-live-id'
        assert response.headers['content-type'] == 'image/jpeg'
