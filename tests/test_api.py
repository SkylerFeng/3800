import asyncio
from dataclasses import replace
import io
import time
import pytest
import httpx
from PIL import Image
from app.config import Settings
from app.main import create_app


@pytest.fixture
async def client():
    app=create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            yield c,app.state.service


async def settle(service):
    if service.task: await service.task
    if service.audio_task: await service.audio_task


def png():
    buf=io.BytesIO();Image.new('RGB',(30,20),'red').save(buf,format='PNG');return buf.getvalue()


async def test_mock_loop_and_missing_imu(client):
    c,s=client
    state=(await c.get('/api/state')).json()
    assert state['imu']['status']=='unconfigured'
    assert state['camera']['frame']['simulated']
    response=await c.post('/api/questions',json={'question':'杯子在哪里','frame_id':state['camera']['frame']['frame_id']})
    assert response.status_code==202
    await settle(s)
    state=(await c.get('/api/state')).json()
    assert state['current_task']['status']=='completed'
    assert '模拟回答，未执行真实图像识别' in state['last_answer']['answer']
    assert state['audio']['status']=='simulated'
    assert state['vision']['external_upload_enabled'] is False


async def test_upload_independent_modes_and_snapshots(client):
    c,s=client
    old=s.camera.frame
    response=await c.post('/api/image',content=png(),headers={'Content-Type':'image/png'})
    assert response.status_code==200
    f=response.json()
    assert f['static'] and not f['simulated']
    state=(await c.get('/api/state')).json()
    assert state['vision']['provider']=='mock' and state['telemetry']['source']=='mock'
    assert (await c.post('/api/questions',json={'question':'颜色','frame_id':old.frame_id})).status_code==409
    await c.post('/api/questions',json={'question':'颜色','frame_id':f['frame_id']})
    await c.post('/api/mode/camera',json={'value':'mock'})
    await settle(s)
    assert s.last_answer['frame_id']==f['frame_id']
    assert (await c.get('/api/frame')).headers['content-type']=='image/jpeg'


@pytest.mark.parametrize('body',[{}, {'question':'','frame_id':'x'},{'question':'   ','frame_id':'x'},{'question':'x'*501,'frame_id':'x'}])
async def test_question_validation(client,body):
    c,_=client
    assert (await c.post('/api/questions',json=body)).status_code==422


async def test_invalid_uploads(client):
    c,s=client
    assert (await c.post('/api/image',content=b'garbage',headers={'Content-Type':'image/png'})).status_code==422
    assert (await c.post('/api/image',content=b'<svg/>',headers={'Content-Type':'image/svg+xml'})).status_code==415
    assert (await c.post('/api/image',content=b'x'*(5*1024*1024+1),headers={'Content-Type':'image/png'})).status_code==413
    assert s.camera.frame.simulated


async def test_distance_fault_expiry_and_haptic(client):
    c,s=client
    result=(await c.post('/api/distance',json={'status':'valid','distance_m':.3})).json()
    assert result['level']=='critical'
    event=s.last_haptic
    assert event['result']['status']=='simulated'
    await c.get('/api/state')
    assert s.last_haptic is event  # throttling
    s.sample=replace(s.sample,received_mono=time.monotonic()-4)
    state=(await c.get('/api/state')).json()
    assert state['distance']['status']=='stale' and state['distance']['level']=='unknown'
    assert state['haptic']['last_event']['request']['stop']
    assert (await c.post('/api/distance',json={'status':'fault','distance_m':None})).json()['level']=='unknown'
    assert (await c.post('/api/distance',json={'status':'valid','distance_m':-1})).status_code==422


async def test_no_silent_fallback(client):
    c,s=client
    await c.post('/api/mode/camera',json={'value':'esp32'})
    assert (await c.get('/api/frame')).status_code==409
    assert (await c.post('/api/questions',json={'question':'什么','frame_id':'x'})).status_code==409
    await c.post('/api/mode/telemetry',json={'value':'esp32'})
    assert (await c.post('/api/distance',json={'status':'valid','distance_m':1})).status_code==409
    state=(await c.get('/api/state')).json()
    assert state['distance']['status']=='unconfigured' and state['camera']['frame'] is None
    assert state['vision']['provider']=='mock'
    await c.post('/api/mode/camera',json={'value':'mock'})
    await c.post('/api/mode/vision',json={'value':'unconfigured'})
    await s.submit('什么',s.camera.frame.frame_id)
    await settle(s)
    assert s.current['status']=='failed'


class Slow:
    async def answer(self,*args):
        await asyncio.sleep(30)
        return 'late'


class Broken:
    async def answer(self,*args):
        raise RuntimeError('SECRET provider response')


@pytest.mark.parametrize('provider,status',[(Slow(),'timeout'),(Broken(),'failed')])
async def test_provider_failure_timeout(provider,status,caplog):
    app=create_app(Settings(model_timeout_s=.03),provider)
    s=app.state.service
    async with app.router.lifespan_context(app):
        await s.submit('问题',s.camera.frame.frame_id)
        await s.distance(__import__('app.domain',fromlist=['DistanceInput']).DistanceInput(distance_m=.2))
        assert (await s.state())['distance']['level']=='critical'
        await settle(s)
        assert s.current['status']==status and s.last_error
        assert 'SECRET' not in caplog.text


async def test_cancel_replacement_and_bounded_events():
    app=create_app(vision=Slow());s=app.state.service
    async with app.router.lifespan_context(app):
        first=await s.submit('1',s.camera.frame.frame_id)
        await asyncio.sleep(.001)
        old=s.current
        second=await s.submit('2',s.camera.frame.frame_id)
        assert first['request_id']!=second['request_id']
        assert old['status']=='cancelled'
        await asyncio.sleep(.001)
        await s.cancel()
        assert s.current['status']=='cancelled'
        for i in range(100): s.event('test',index=i)
        assert len(s.events)==40


async def test_stale_live_frame_but_static_allowed(client):
    c,s=client
    s.camera.frame=replace(s.camera.frame,static=False,received_mono=time.monotonic()-20)
    assert (await c.post('/api/questions',json={'question':'什么','frame_id':s.camera.frame.frame_id})).status_code==409
    s.camera.frame=replace(s.camera.frame,static=True)
    assert (await c.post('/api/questions',json={'question':'什么','frame_id':s.camera.frame.frame_id})).status_code==202
    await settle(s)


async def test_local_page_and_origin(client):
    c,s=client
    assert (await c.get('/')).status_code==200
    assert (await c.get('/static/app.js')).status_code==200
    r=await c.post('/api/distance',json={'status':'fault'},headers={'Origin':'https://example.com'})
    assert r.status_code==403
    assert (await c.get('/api/state')).headers['cache-control']=='no-store'


async def test_unconfigured_startup():
    app=create_app(Settings(camera_source='esp32',telemetry_source='esp32',vision_provider='unconfigured',tts_provider='unconfigured',haptic_output='unconfigured'))
    async with app.router.lifespan_context(app):
        state=await app.state.service.state()
        assert state['camera']['frame'] is None
        assert state['distance']['level']=='unknown'
        assert state['haptic']['status']=='unconfigured'


async def test_cancel_before_worker_starts():
    app=create_app(vision=Slow());s=app.state.service
    async with app.router.lifespan_context(app):
        await s.submit('一',s.camera.frame.frame_id)
        await s.cancel()
        assert s.current['status']=='cancelled'
        await s.submit('二',s.camera.frame.frame_id)
        old=s.current
        await s.submit('三',s.camera.frame.frame_id)
        assert old['status']=='cancelled'
        await s.cancel()


async def test_slow_tts_does_not_block_telemetry(client):
    c,s=client
    class SlowTTS:
        def __init__(self): self.cancelled=False;self.started=asyncio.Event()
        async def synthesize(self,text):
            self.started.set()
            try: await asyncio.sleep(30)
            except asyncio.CancelledError: self.cancelled=True;raise
    tts=SlowTTS();s.tts=tts
    await s.submit('一',s.camera.frame.frame_id)
    await s.task
    await tts.started.wait()
    response=await asyncio.wait_for(c.post('/api/distance',json={'status':'valid','distance_m':.2}),.3)
    assert response.json()['level']=='critical'
    await s.submit('二',s.camera.frame.frame_id)
    assert tts.cancelled
    await s.cancel()


async def test_pixel_limit(client):
    c,s=client
    buf=io.BytesIO();Image.new('1',(4000,3001)).save(buf,format='PNG')
    response=await c.post('/api/image',content=buf.getvalue(),headers={'Content-Type':'image/png'})
    assert response.status_code==422


async def test_unconfigured_audio_and_haptic():
    from app.domain import DistanceInput
    app=create_app(Settings(tts_provider='unconfigured',haptic_output='unconfigured'))
    s=app.state.service
    async with app.router.lifespan_context(app):
        await s.submit('问题',s.camera.frame.frame_id)
        await settle(s)
        assert s.audio_event['status']=='unconfigured'
        await s.distance(DistanceInput(distance_m=.2))
        assert s.last_haptic['result']['status']=='unconfigured'
