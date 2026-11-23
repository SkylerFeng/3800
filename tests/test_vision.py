import asyncio
import base64
import json
import httpx
import pytest
from app.config import Settings
from app.main import create_app
from app.providers import LatestCamera
from app.vision import HTTPVisionProvider, VisionError, vision_status


def configured(**kwargs):
    return Settings(vision_api_base_url='https://vision.example/v1', vision_api_model='test-vision',
                    vision_api_key='test-secret', allow_external_vision=True, ollama_model='test-local', **kwargs)


@pytest.mark.parametrize('name', ['openai_compatible', 'ollama'])
async def test_wire_format(name):
    frame=LatestCamera().mock()
    def handler(request):
        data=json.loads(request.content)
        assert data['stream'] is False
        assert '环境数据' in data['messages'][0]['content']
        if name=='openai_compatible':
            assert request.url.path=='/v1/chat/completions'
            assert request.headers['authorization']=='Bearer test-secret'
            parts=data['messages'][1]['content']
            assert parts[0]['text']=='杯子在哪里'
            assert base64.b64decode(parts[1]['image_url']['url'].split(',')[1])==frame.content
            return httpx.Response(200,json={'choices':[{'message':{'content':'杯子在当前画面右侧。'}}]})
        assert request.url.path=='/api/chat'
        assert 'authorization' not in request.headers
        assert base64.b64decode(data['messages'][1]['images'][0])==frame.content
        return httpx.Response(200,json={'done':True,'message':{'content':'杯子在当前画面右侧。'}})
    provider=HTTPVisionProvider(configured(),name,httpx.MockTransport(handler))
    assert await provider.answer(frame,'杯子在哪里')=='杯子在当前画面右侧。'


@pytest.mark.parametrize('config,name',[
    (Settings(),'openai_compatible'),
    (Settings(vision_api_base_url='https://vision.example/v1',vision_api_model='x'),'openai_compatible'),
    (Settings(),'ollama'),
])
async def test_no_config_or_consent_no_request(config,name):
    def handler(request): pytest.fail('Unexpected network request')
    with pytest.raises(VisionError):
        await HTTPVisionProvider(config,name,httpx.MockTransport(handler)).answer(LatestCamera().mock(),'问题')


@pytest.mark.parametrize('status,word',[(401,'认证'),(403,'认证'),(429,'配额'),(500,'请求失败'),(302,'请求失败')])
async def test_redacted_errors(status,word):
    provider=HTTPVisionProvider(configured(),'openai_compatible',httpx.MockTransport(lambda r:httpx.Response(status,text='test-secret sensitive data')))
    with pytest.raises(VisionError,match=word) as error:
        await provider.answer(LatestCamera().mock(),'问题')
    assert 'test-secret' not in str(error.value)


@pytest.mark.parametrize('payload',[{}, {'choices':[]}, {'choices':[{'message':{'content':''}}]}, {'choices':[{'message':{'content':['invalid']}}]}, {'choices':[{'message':{'content':'x'},'finish_reason':'length'}]}])
async def test_bad_responses(payload):
    provider=HTTPVisionProvider(configured(),'openai_compatible',httpx.MockTransport(lambda r:httpx.Response(200,json=payload)))
    with pytest.raises(VisionError): await provider.answer(LatestCamera().mock(),'问题')


async def test_response_limit():
    provider=HTTPVisionProvider(configured(),'ollama',httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*65537)))
    with pytest.raises(VisionError,match='过大'): await provider.answer(LatestCamera().mock(),'问题')


@pytest.mark.parametrize('error,word',[(httpx.ConnectError('private'),'无法连接'),(httpx.ReadTimeout('private'),'超时')])
async def test_network_errors(error,word):
    def handler(request): raise error
    provider=HTTPVisionProvider(configured(),'ollama',httpx.MockTransport(handler))
    with pytest.raises(VisionError,match=word): await provider.answer(LatestCamera().mock(),'问题')


async def test_timeout_cancels_adapter_but_telemetry_runs():
    started=asyncio.Event();cancelled=asyncio.Event()
    async def handler(request):
        started.set()
        try: await asyncio.sleep(30)
        except asyncio.CancelledError: cancelled.set();raise
    config=configured(vision_provider='openai_compatible',model_timeout_s=.05)
    app=create_app(config,HTTPVisionProvider(config,'openai_compatible',httpx.MockTransport(handler)))
    service=app.state.service
    async with app.router.lifespan_context(app):
        await service.submit('问题',service.camera.frame.frame_id)
        await started.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.post('/api/distance',json={'status':'valid','distance_m':.2})).json()['level']=='critical'
        await service.task
        assert service.current['status']=='timeout' and cancelled.is_set()


@pytest.mark.parametrize('url',['http://remote.example/v1','https://user:secret@example.com/v1','https://example.com/v1?key=secret','file:///tmp/model'])
def test_bad_urls(url):
    with pytest.raises(ValueError): Settings(vision_api_base_url=url)


def test_local_only_ollama_and_secret_repr():
    with pytest.raises(ValueError): Settings(ollama_base_url='https://remote.example')
    assert 'test-secret' not in repr(configured())
    assert 'test-secret' not in json.dumps(vision_status(configured(),'openai_compatible'))


@pytest.mark.parametrize('raw,expected',[('false',False),('0',False),('true',True),('1',True)])
def test_boolean_env(monkeypatch,raw,expected):
    monkeypatch.setenv('ALLOW_EXTERNAL_VISION',raw)
    assert Settings.from_env().allow_external_vision is expected


@pytest.mark.parametrize('name', ['openai_compatible', 'ollama'])
async def test_real_local_socket_roundtrip(name):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            result=({'choices':[{'message':{'content':'本地协议测试，不是真实识别。'}}]} if name=='openai_compatible'
                    else {'done':True,'message':{'content':'本地协议测试，不是真实识别。'}})
            body=json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root=f'http://127.0.0.1:{server.server_port}'
    config=Settings(vision_provider=name,vision_api_base_url=root+'/v1',vision_api_model='wire-test',ollama_base_url=root,ollama_model='wire-test')
    app=create_app(config);service=app.state.service
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                state=(await client.get('/api/state')).json()
                response=await client.post('/api/questions',json={'question':'图片里有什么','frame_id':state['camera']['frame']['frame_id']})
                assert response.status_code==202
                await service.task
                state=(await client.get('/api/state')).json()
                assert state['current_task']['status']=='completed'
                assert not state['current_task']['simulated']
                assert state['current_task']['provider']==name
                assert '协议测试' in state['last_answer']['answer']
                assert state['telemetry']['source']=='mock'
                assert not state['vision']['external_upload_enabled']
        assert received[0][0]==('/v1/chat/completions' if name=='openai_compatible' else '/api/chat')
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close();thread.join()


async def test_runtime_mode_and_consent_state_no_fallback():
    config=Settings(vision_api_base_url='https://vision.example/v1',vision_api_model='vision-model',vision_api_key='test-secret')
    app=create_app(config);service=app.state.service
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            state=(await client.post('/api/mode/vision',json={'value':'openai_compatible'})).json()
            assert state['vision']['status']=='disabled'
            assert state['camera']['frame']['simulated']
            await service.submit('问题',service.camera.frame.frame_id)
            await service.task
            assert service.current['status']=='failed'
            assert 'ALLOW_EXTERNAL_VISION' in service.current['error']
            assert service.last_answer is None
            state=(await client.get('/api/state')).json()
            assert 'test-secret' not in json.dumps(state)
            state=(await client.post('/api/mode/vision',json={'value':'mock'})).json()
            assert state['vision']['status']=='simulated'
