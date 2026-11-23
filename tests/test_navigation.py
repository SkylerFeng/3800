import asyncio
import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from app.config import Settings
from app.domain import utc_now
from app.main import create_app
from app.maps import AmapProvider, MockMapProvider, MOCK_START, MOCK_CORNER, MOCK_END
from app.navigation import Navigation
from app.nav_types import NavError, PositionInput


def fix(point=MOCK_START, source='mock', **extra):
    return PositionInput(**point.model_dump(), source=source, accuracy_m=extra.pop('accuracy_m',3), captured_at=utc_now(), **extra)


async def ready(nav):
    await nav.position(fix())
    s=await nav.search('图书馆','')
    return await nav.plan(s['search_id'],s['candidates'][0]['candidate_id'])


async def test_complete_simulated_course():
    n=Navigation(Settings())
    assert (await ready(n))['status']=='preview'
    assert n.state()['instruction'] is None
    assert n.start()['status']=='navigating'
    for p in [MOCK_START.model_copy(update={'longitude':116.4005}),MOCK_CORNER,
              MOCK_CORNER.model_copy(update={'latitude':39.9005}),MOCK_END]:
        await n.position(fix(p))
    s=await n.position(fix(MOCK_END))
    assert s['status']=='near_destination' and s['instruction'] is None
    assert '确认' in s['message']
    assert len(s['candidates'])==2


async def test_expired_poor_and_fault_location_suppress_instructions():
    n=Navigation(Settings());await ready(n);n.start()
    n.location['received_mono']-=20
    assert n.state()['status']=='location_unavailable' and n.state()['instruction'] is None
    await n.position(fix(accuracy_m=100))
    assert n.state()['instruction'] is None
    await n.position(fix())
    assert n.state()['instruction']
    n.fault('denied')
    assert n.state()['instruction'] is None


async def test_expired_route_pause_and_resume():
    n=Navigation(Settings());await ready(n);n.start()
    assert n.pause()['instruction'] is None
    assert n.start()['instruction']
    n.route_mono-=2000
    assert n.state()['status']=='route_expired'
    with pytest.raises(NavError): n.start()
    assert n.stop()['route'] is None


async def test_off_route_requires_distinct_samples_not_polls():
    n=Navigation(Settings());await ready(n);n.start()
    off=MOCK_START.model_copy(update={'latitude':39.899})
    await n.position(fix(off))
    assert n.state()['status']=='checking_position'
    for _ in range(10): n.state()
    assert n.deviations==1
    await n.position(fix(off));await n.position(fix(off))
    assert n.state()['status']=='off_route' and n.state()['instruction'] is None
    await n.position(fix())
    assert n.state()['status']=='off_route'


async def test_invalid_candidate_and_mode_change_clear_data():
    n=Navigation(Settings());await n.position(fix());s=await n.search('图书馆','')
    with pytest.raises(NavError): await n.plan(s['search_id'],'forged')
    assert n.route is None
    await n.plan(s['search_id'],s['candidates'][0]['candidate_id'])
    with pytest.raises(NavError): await n.plan(s['search_id'],s['candidates'][0]['candidate_id'])
    await n.set_mode('amap')
    assert n.location is None and n.route is None and n.candidates==[]
    with pytest.raises(NavError,match='模拟位置'): await n.position(fix())
    with pytest.raises(NavError,match='AMAP_API_KEY'): await n.search('学校','北京')
    assert n.mode=='amap' and n.route is None


async def test_manual_preview_not_dynamic_navigation():
    class FakeReal(MockMapProvider):
        async def route(self,origin,destination):
            route=await super().route(origin,destination)
            return route.model_copy(update={'source':'amap'})
    n=Navigation(Settings(map_provider='amap'),FakeReal())
    await n.position(fix(source='manual'))
    s=await n.search('图书馆','');await n.plan(s['search_id'],s['candidates'][0]['candidate_id'])
    with pytest.raises(NavError,match='手动'): n.start()


async def test_map_timeout_does_not_block_health_and_no_fallback():
    class SlowMap(MockMapProvider):
        async def candidates(self,*args): await asyncio.sleep(30)
    app=create_app(Settings(map_timeout_s=.03));n=app.state.service.navigation
    n.provider=SlowMap()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            token=(await c.get('/api/navigation/session')).json()['token']
            headers={'X-Navigation-Token':token}
            task=asyncio.create_task(c.post('/api/navigation/search',json={'query':'x'},headers=headers))
            await asyncio.sleep(.005)
            assert (await c.get('/api/health')).status_code==200
            assert (await task).status_code==409
            assert n.route is None and n.status=='error'


async def test_navigation_api_auth_validation_and_obstacle_priority():
    app=create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.get('/api/navigation/state')).status_code==403
            h={'X-Navigation-Token':(await c.get('/api/navigation/session')).json()['token']}
            for body in [{'query':' '},{'query':'x'*81}]:
                assert (await c.post('/api/navigation/search',json=body,headers=h)).status_code==422
            r=await c.post('/api/navigation/position',json={'longitude':181,'latitude':2,'coordinate_system':'WGS84','source':'browser','accuracy_m':1,'captured_at':utc_now()},headers=h)
            assert r.status_code==422
            await c.post('/api/navigation/mock-position',json={'point':'start'},headers=h)
            s=(await c.post('/api/navigation/search',json={'query':'图书馆'},headers=h)).json()
            assert (await c.post('/api/navigation/plan',json={'search_id':s['search_id'],'candidate_id':s['candidates'][0]['candidate_id']},headers=h)).status_code==200
            await c.post('/api/navigation/start',headers=h)
            for distance in (.3, .5, .8):
                await c.post('/api/distance', json={'status': 'valid', 'distance_m': distance})
                s = (await c.get('/api/navigation/state', headers=h)).json()
                assert s['obstacle_priority'] and s['instruction'] is None
            await c.post('/api/distance', json={'status': 'valid', 'distance_m': .81})
            s = (await c.get('/api/navigation/state', headers=h)).json()
            assert not s['obstacle_priority'] and s['instruction']
            assert (await c.get('/navigation')).status_code==200
            await c.post('/api/navigation/mode',json={'value':'amap'},headers=h)
            assert (await c.post('/api/navigation/mock-position',json={'point':'start'},headers=h)).status_code==409


def real_provider(handler,**kwargs):
    return AmapProvider(Settings(amap_api_key='test-map-secret',allow_external_maps=True,**kwargs),httpx.MockTransport(handler))


async def test_amap_contract_search_convert_and_route(caplog):
    caplog.set_level(logging.INFO)
    paths=[]
    def handler(request):
        paths.append(request.url.path)
        assert request.url.host=='restapi.amap.com'
        assert request.url.params['key']=='test-map-secret'
        if request.url.path=='/v5/place/text':
            assert request.url.params['keywords']=='图书馆' and request.url.params['region']=='北京'
            return httpx.Response(200,json={'status':'1','pois':[{'id':'poi1','name':'测试入口','address':'测试地址','location':MOCK_END.pair()}]})
        if 'convert' in request.url.path:
            assert request.url.params['coordsys']=='gps'
            return httpx.Response(200,json={'status':'1','locations':MOCK_START.pair()})
        assert request.url.params['show_fields']=='polyline,navi,cost'
        assert request.url.params['destination_id']=='poi1'
        return httpx.Response(200,json={'status':'1','route':{'paths':[{'distance':'196','steps':[
            {'instruction':'沿测试道路前进','step_distance':'85','polyline':MOCK_START.pair()+';'+MOCK_CORNER.pair(),'road_name':'测试道路','navi':{'action':'直行'}},
            {'instruction':'向左转','step_distance':'111','polyline':MOCK_CORNER.pair()+';'+MOCK_END.pair(),'navi':{'action':'左转'}}]}]}})
    p=real_provider(handler)
    dest=(await p.candidates('图书馆','北京'))[0]
    source=await p.to_map_point(MOCK_START.model_copy(update={'coordinate_system':'WGS84'}))
    route=await p.route(source,dest)
    assert route.source=='amap' and len(route.steps)==2
    assert route.steps[1].action=='左转'
    assert len(paths)==3
    assert 'test-map-secret' not in caplog.text
    assert 'test-map-secret' not in repr(p.config)
    # Already-normalized coordinates must not be converted a second time.
    assert await p.to_map_point(source)==source and len(paths)==3


@pytest.mark.parametrize('response',[
    httpx.Response(401,text='test-map-secret'),
    httpx.Response(302,headers={'Location':'https://example.com'}),
    httpx.Response(200,json={'status':'0','infocode':'10001','info':'test-map-secret'}),
    httpx.Response(200,json={'status':'0','infocode':'10003'}),
    httpx.Response(200,text='not json'),
    httpx.Response(200,json=['invalid']),
])
async def test_amap_errors_redacted(response):
    p=real_provider(lambda request:response)
    with pytest.raises(NavError) as err: await p.candidates('x','')
    assert 'test-map-secret' not in str(err.value)


@pytest.mark.parametrize('config',[Settings(map_provider='amap'),Settings(map_provider='amap',amap_api_key='test-map-secret')])
async def test_missing_key_or_consent_never_calls(config):
    def fail(request): pytest.fail('unapproved network request')
    with pytest.raises(NavError): await AmapProvider(config,httpx.MockTransport(fail)).candidates('x','')


async def test_malformed_route_and_empty_results():
    p=real_provider(lambda r:httpx.Response(200,json={'status':'1','pois':[]}))
    assert await p.candidates('x','')==[]
    dest=(await MockMapProvider().candidates('x'))[0]
    for payload in [{'status':'1','route':{'paths':[]}}, {'status':'1','route':{'paths':[{'distance':100,'steps':[]}]}}]:
        p=real_provider(lambda r:httpx.Response(200,json=payload))
        with pytest.raises(NavError): await p.route(MOCK_START,dest)


async def test_location_fault_during_conversion_discards_late_result():
    gate=asyncio.Event()
    class Delayed(MockMapProvider):
        async def to_map_point(self,point):
            await gate.wait();return point
    n=Navigation(Settings(),Delayed())
    task=asyncio.create_task(n.position(fix()))
    await asyncio.sleep(0)
    n.fault('stopped');gate.set()
    with pytest.raises(NavError): await task
    assert n.location is None


@pytest.mark.parametrize('seconds_older', [0, 1])
async def test_duplicate_or_older_fix_does_not_invalidate_or_refresh_latest(seconds_older):
    n=Navigation(Settings());await ready(n);n.start()
    old=n.location['input']
    replay = old.model_copy(update={'captured_at': old.captured_at - timedelta(seconds=seconds_older)})
    received = n.location['received_mono']
    version = n.location_version
    assert (await n.position(replay))['instruction']
    assert n.location_version == version and n.location['received_mono'] == received

    n.location['received_mono'] -= n.config.location_ttl_s + 1
    result = await n.position(replay)
    assert result['status'] == 'location_unavailable'
    assert result['instruction'] is None


async def test_plan_cancel_discards_late_route():
    gate=asyncio.Event()
    class Delayed(MockMapProvider):
        async def route(self,origin,destination):
            await gate.wait()
            return await super().route(origin,destination)
    n=Navigation(Settings(),Delayed());await n.position(fix());s=await n.search('图书馆','')
    task=asyncio.create_task(n.plan(s['search_id'],s['candidates'][0]['candidate_id']))
    await asyncio.sleep(0)
    n.stop();gate.set()
    with pytest.raises(NavError): await task
    assert n.route is None and not n.active


async def test_amap_network_timeout_and_big_response():
    def timeout(request): raise httpx.ReadTimeout('private-key-and-location')
    with pytest.raises(NavError,match='超时'):
        await real_provider(timeout).candidates('x','')
    def offline(request): raise httpx.ConnectError('private')
    with pytest.raises(NavError,match='无法连接'):
        await real_provider(offline).candidates('x','')
    with pytest.raises(NavError,match='大小限制'):
        await real_provider(lambda r:httpx.Response(200,content=b'x'*(2*1024*1024+1))).candidates('x','')


async def test_amap_adapter_over_local_socket():
    # Redirect transport destination only for this test; app URL remains the official host.
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            body=json.dumps({'status':'1','pois':[{'id':'test','name':'测试地点','address':'测试地址','location':MOCK_END.pair()}]}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)))
            self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    class LocalTransport(httpx.AsyncBaseTransport):
        def __init__(self): self.inner=httpx.AsyncHTTPTransport()
        async def handle_async_request(self,request):
            request.url=request.url.copy_with(scheme='http',host='127.0.0.1',port=server.server_port)
            return await self.inner.handle_async_request(request)
        async def aclose(self): await self.inner.aclose()
    try:
        p=AmapProvider(Settings(amap_api_key='test-map-secret',allow_external_maps=True),LocalTransport())
        values=await p.candidates('测试','北京')
        assert values[0].source=='amap' and values[0].candidate_id=='test'
        assert received and received[0].startswith('/v5/place/text?')
    finally:
        await asyncio.to_thread(server.shutdown);server.server_close();thread.join()


async def test_off_route_remains_latched_after_return_and_new_deviation():
    n=Navigation(Settings());await ready(n);n.start()
    off=MOCK_START.model_copy(update={'latitude':39.899})
    for _ in range(3): await n.position(fix(off))
    await n.position(fix())
    await n.position(fix(off))
    assert n.state()['status']=='off_route' and n.state()['instruction'] is None


async def test_pause_resume_does_not_count_same_arrival_fix_twice():
    n=Navigation(Settings());await ready(n);n.start()
    for point in [MOCK_CORNER,MOCK_CORNER.model_copy(update={'latitude':39.9005}),MOCK_END]: await n.position(fix(point))
    assert n.arrivals==1
    n.pause();n.start()
    assert n.arrivals==1 and n.state()['status']=='navigating'


async def test_stale_gap_resets_confirmation_samples():
    n=Navigation(Settings());await ready(n);n.start()
    off=MOCK_START.model_copy(update={'latitude':39.899})
    await n.position(fix(off));await n.position(fix(off))
    n.location['received_mono']-=20
    await n.position(fix(off))
    assert n.deviations==1 and n.state()['status']=='checking_position'


@pytest.mark.parametrize('endpoint', ['起点', '终点'])
async def test_route_endpoint_error_identifies_which_end_to_correct(endpoint):
    class ShiftedRoute(MockMapProvider):
        async def route(self, origin, destination):
            route = await super().route(origin, destination)
            step = route.steps[0 if endpoint == '起点' else -1]
            index = 0 if endpoint == '起点' else -1
            point = step.geometry[index]
            step.geometry[index] = point.model_copy(update={'latitude': point.latitude + .002})
            return route

    nav = Navigation(Settings(), ShiftedRoute())
    with pytest.raises(NavError, match=f'高德路线{endpoint}距.*约 222 米'):
        await ready(nav)
    assert nav.route is None
    assert nav.state()['instruction'] is None


async def test_position_updates_while_candidate_search_is_pending():
    gate = asyncio.Event()
    started = asyncio.Event()

    class SlowSearch(MockMapProvider):
        async def candidates(self, query, region=''):
            started.set()
            await gate.wait()
            return await super().candidates(query, region)

    nav = Navigation(Settings(), SlowSearch())
    search = asyncio.create_task(nav.search('图书馆', ''))
    try:
        await started.wait()
        position = await asyncio.wait_for(nav.position(fix()), 1)
        assert position['location']['status'] == 'valid'
    finally:
        gate.set()
        await search


async def test_amap_search_pages_skip_invalid_and_duplicate_pois():
    valid = {'id': 'poi1', 'name': '图书馆', 'location': MOCK_END.pair()}

    def handler(request):
        assert request.url.params['page_size'] == '20'
        assert request.url.params['page_num'] == '2'
        return httpx.Response(200, json={'status': '1', 'pois': [
            {'name': '没坐标的候选'}, valid, valid,
        ]})

    result = await real_provider(handler).candidates('图书馆', '北京', page=2)
    assert [candidate.candidate_id for candidate in result] == ['poi1']
