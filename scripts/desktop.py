"""Expose the running hotspot instance on the desktop's loopback address."""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import ssl

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, help='Hotspot HTTPS base URL')
    parser.add_argument('--ca', type=Path, default=Path('certs/ca/rootCA.pem'))
    args = parser.parse_args()
    upstream = httpx.URL(args.upstream)
    if upstream.scheme != 'https' or not upstream.host or upstream.path != '/' or upstream.query:
        parser.error('--upstream 需要填写 HTTPS 服务根地址')
    desktop = 'http://127.0.0.1:8001'
    upstream_origin = str(upstream).rstrip('/')

    @asynccontextmanager
    async def lifespan(app):
        tls = ssl.create_default_context(cafile=str(args.ca))
        async with httpx.AsyncClient(verify=tls, trust_env=False, timeout=120) as client:
            app.state.client = client
            yield

    async def forward(request: Request):
        if request.headers.get('host') != '127.0.0.1:8001':
            return PlainTextResponse('请使用 127.0.0.1:8001', status_code=400)
        origin = request.headers.get('origin')
        if origin and origin != desktop:
            return PlainTextResponse('跨来源请求已拒绝', status_code=403)
        hop_headers = {'host', 'connection', 'transfer-encoding', 'keep-alive',
                       'upgrade', 'proxy-authorization', 'proxy-authenticate', 'te', 'trailer'}
        hop_headers.update(name.strip().lower() for name in request.headers.get('connection', '').split(','))
        headers = [(k, v) for k, v in request.headers.items() if k not in hop_headers and k != 'origin']
        if origin:
            headers.append(('origin', upstream_origin))
        raw_path = request.scope['raw_path']
        query = request.scope['query_string']
        target = upstream.copy_with(raw_path=raw_path + (b'?' + query if query else b''))
        client = request.app.state.client
        body = request.stream() if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} else None
        try:
            response = await client.send(
                client.build_request(request.method, target, headers=headers, content=body),
                stream=True,
            )
        except httpx.HTTPError:
            return PlainTextResponse('无法连接热点 HTTPS 服务，请检查 8443 是否运行。', status_code=502)
        reply_headers = {k: v for k, v in response.headers.items() if k not in hop_headers}
        location = reply_headers.get('location')
        if location and location.startswith(upstream_origin + '/'):
            reply_headers['location'] = desktop + location[len(upstream_origin):]
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                 headers=reply_headers, background=BackgroundTask(response.aclose))

    app = Starlette(lifespan=lifespan, routes=[
        Route('/{path:path}', forward, methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']),
    ])
    uvicorn.run(app, host='127.0.0.1', port=8001, access_log=False)


if __name__ == '__main__':
    main()
