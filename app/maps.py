"""Amap Web Service adapters; no SDK guesses, external calls require opt-in."""
import asyncio
import json
import logging
import uuid
from typing import Protocol
import httpx
from .config import Settings
from .nav_types import Candidate, NavError, NavRoute, NavStep, Point


class RouteProvider(Protocol):
    async def candidates(self, query: str, region: str, *, page: int = 1) -> list[Candidate]: ...
    async def to_map_point(self, point: Point) -> Point: ...
    async def route(self, origin: Point, destination: Candidate) -> NavRoute: ...


def coordinate(value: str) -> Point:
    lon, lat = value.split(',')
    return Point(longitude=float(lon), latitude=float(lat), coordinate_system='GCJ02')


class _HideAmapURL(logging.Filter):
    def filter(self, record):
        return 'restapi.amap.com' not in record.getMessage()


# HTTPX INFO request logs contain query parameters (Amap requires key in query).
logging.getLogger('httpx').addFilter(_HideAmapURL())
logging.getLogger('httpcore').setLevel(logging.WARNING)


class AmapProvider:
    def __init__(self, config: Settings, transport=None):
        self.config = config
        self.transport = transport

    async def request(self, path, params):
        if not self.config.amap_api_key:
            raise NavError('未配置 AMAP_API_KEY（需要高德 Web 服务类型 Key）')
        if not self.config.allow_external_maps:
            raise NavError('高德外发未启用；请明确设置 ALLOW_EXTERNAL_MAPS=true')
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(self.config.map_timeout_s, connect=min(5, self.config.map_timeout_s)),
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                async with client.stream('GET', 'https://restapi.amap.com' + path,
                        params={**params, 'key': self.config.amap_api_key, 'output': 'json'}) as response:
                    if response.status_code != 200:
                        raise NavError('高德服务请求失败或限流，请检查权限和配额')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 2 * 1024 * 1024:
                            raise NavError('高德响应超出大小限制')
                        data.extend(chunk)
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            if str(result.get('status')) != '1':
                code = str(result.get('infocode', ''))
                if code in ('10001', '10002', '10005', '10007', '10009', '10012', '10013'):
                    raise NavError('高德 Key 或权限不正确，请检查 Web 服务 Key 和访问限制')
                if code in ('10003', '10004', '10014', '10019', '10020', '10021'):
                    raise NavError('高德调用额度或频率受限，请检查配额')
                raise NavError('高德未返回可用结果，请检查区域、参数或服务状态')
            return result
        except httpx.TimeoutException:
            raise NavError('高德请求超时，已暂停具体导航提示') from None
        except httpx.RequestError:
            raise NavError('无法连接高德服务，已暂停具体导航提示') from None
        except (ValueError, TypeError):
            raise NavError('高德响应格式无效') from None

    async def candidates(self, query, region='', *, page=1):
        params = {'keywords': query, 'page_size': '20', 'page_num': str(page)}
        if region:
            params.update(region=region, city_limit='true')
        data = await self.request('/v5/place/text', params)
        try:
            pois = data.get('pois', [])
            if not isinstance(pois, list):
                raise ValueError()
            candidates = []
            seen = set()
            for poi in pois[:20]:
                try:
                    candidate = Candidate(
                        candidate_id=poi['id'], label=poi['name'],
                        address=poi.get('address') or '', point=coordinate(poi['location']), source='amap',
                    )
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue
                if candidate.candidate_id not in seen:
                    candidates.append(candidate)
                    seen.add(candidate.candidate_id)
            return candidates
        except (KeyError, ValueError, TypeError, AttributeError):
            raise NavError('高德地点数据不完整，无法提供可靠候选') from None

    async def to_map_point(self, point):
        if point.coordinate_system == 'GCJ02':
            return point
        result = await self.request('/v3/assistant/coordinate/convert',
                                    {'locations': point.pair(), 'coordsys': 'gps'})
        try:
            return coordinate(result['locations'])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise NavError('坐标转换失败；没有继续使用未经转换的坐标') from None

    async def route(self, origin, destination):
        if origin.coordinate_system != 'GCJ02' or destination.point.coordinate_system != 'GCJ02':
            raise NavError('路线坐标系不一致，必须先转换为 GCJ02')
        data = await self.request('/v5/direction/walking', {
            'origin': origin.pair(), 'destination': destination.point.pair(),
            'destination_id': destination.candidate_id, 'show_fields': 'polyline,navi,cost',
            'isindoor': '0', 'alternative_route': '1'})
        try:
            paths = data['route']['paths']
            if not paths:
                raise NavError('没有可用步行路线，请更换目的地或入口')
            path = paths[0]
            steps = [NavStep(instruction=s['instruction'], distance_m=float(s['step_distance']),
                    geometry=[coordinate(p) for p in s['polyline'].split(';')],
                    action=(s.get('navi') or {}).get('action') or '', road_name=s.get('road_name') or '',
                    walk_type=str((s.get('navi') or {}).get('walk_type') or s.get('walk_type') or ''))
                    for s in path['steps']]
            if sum(len(s.geometry) for s in steps) > 20000:
                raise NavError('路线过于复杂，超过本原型可处理范围')
            return NavRoute(route_id=uuid.uuid4().hex, source='amap', origin=origin,
                            destination=destination, distance_m=float(path['distance']), steps=steps)
        except (KeyError, ValueError, TypeError, AttributeError, IndexError):
            raise NavError('路线缺少有效指引或几何信息，不能启动导航') from None


# Fictional, explicitly simulated test course, not actual roads / campus / POIs.
MOCK_START = Point(longitude=116.4000, latitude=39.9000, coordinate_system='GCJ02')
MOCK_CORNER = Point(longitude=116.4010, latitude=39.9000, coordinate_system='GCJ02')
MOCK_END = Point(longitude=116.4010, latitude=39.9010, coordinate_system='GCJ02')


class MockMapProvider:
    async def candidates(self, query, region='', *, page=1):
        return [Candidate(candidate_id='mock-library', label='模拟图书馆入口（虚构）',
                          address='仅供软件回放，不是真实地点', point=MOCK_END, source='mock'),
                Candidate(candidate_id='mock-library-2', label='模拟图书馆另一入口（虚构）',
                          address='用于验证候选确认', point=MOCK_END, source='mock')]

    async def to_map_point(self, point):
        if point.coordinate_system != 'GCJ02':
            raise NavError('模拟模式不转换真实位置；请使用内置模拟起点')
        return point

    async def route(self, origin, destination):
        if origin != MOCK_START:
            raise NavError('模拟路线只接受内置模拟起点，不为真实位置编造道路')
        return NavRoute(route_id=uuid.uuid4().hex, source='mock', origin=origin, destination=destination,
            distance_m=196, steps=[
                NavStep(instruction='模拟步骤：沿虚构演示线向东前进至转角。', distance_m=85, geometry=[MOCK_START, MOCK_CORNER]),
                NavStep(instruction='模拟步骤：在虚构转角向左转，向北前进。', distance_m=111, geometry=[MOCK_CORNER, MOCK_END], action='左转')])


class UnconfiguredMapProvider:
    async def candidates(self, query, region='', *, page=1):
        raise NavError('地图未配置，请选择模拟或配置高德 Web 服务 Key')

    async def to_map_point(self, point):
        raise NavError('地图未配置')

    async def route(self, origin, destination):
        raise NavError('地图未配置')


def build_map(config, name):
    if name == 'amap':
        return AmapProvider(config)
    return MockMapProvider() if name == 'mock' else UnconfiguredMapProvider()
