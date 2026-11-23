"""One in-memory navigation session; never asks an LLM to create routes."""
import asyncio
from datetime import datetime, timezone
import math
import secrets
import time
import uuid
from .domain import utc_now
from .maps import build_map, MOCK_START, MOCK_CORNER, MOCK_END
from .nav_types import NavError, Point, PositionInput


def meters(a: Point, b: Point):
    x = math.radians(a.longitude-b.longitude) * math.cos(math.radians((a.latitude+b.latitude)/2))
    y = math.radians(a.latitude-b.latitude)
    return math.hypot(x,y)*6371000


def project(point, route, progress):
    """Nearest segment projection in a small local tangent plane, with bounded forward matching."""
    offset=0; matches=[]
    for index, step in enumerate(route.steps):
        for a,b in zip(step.geometry,step.geometry[1:]):
            length=meters(a,b)
            if length < .01:
                continue
            scale=math.cos(math.radians(point.latitude))
            ax=math.radians(a.longitude-point.longitude)*scale*6371000
            ay=math.radians(a.latitude-point.latitude)*6371000
            bx=math.radians(b.longitude-point.longitude)*scale*6371000
            by=math.radians(b.latitude-point.latitude)*6371000
            t=max(0,min(1,-(ax*(bx-ax)+ay*(by-ay))/(length*length)))
            along=offset+t*length
            matches.append((math.hypot(ax+t*(bx-ax),ay+t*(by-ay)), along, index))
            offset+=length
    if not matches:
        raise NavError('路线几何无效')
    # Do not jump to a distant crossing / parallel route section in one fix.
    nearby=[v for v in matches if max(0,progress-30)<=v[1]<=progress+100]
    best=min(nearby or matches,key=lambda v:(v[0],abs(v[1]-progress)))
    return best,offset


class Navigation:
    def __init__(self,config,provider=None):
        self.config=config
        self.mode=config.map_provider
        self.provider=provider or build_map(config,self.mode)
        self.token=secrets.token_urlsafe(24)
        self.lock=asyncio.Lock()
        self.position_lock = asyncio.Lock()
        self.location=None
        self.location_fault=None
        self.location_version=0
        self.search_id=None
        self.candidates=[]
        self.route=None
        self.route_mono=0
        self.status='idle'
        self.error=None
        self.active=False
        self.user_paused=False
        self.step_index=0
        self.progress=0
        self.route_distance=None
        self.deviations=0
        self.arrivals=0
        self.last_match_version=-1
        self.used_search=False
        self.generation=0

    def summary(self):
        return {'provider':self.mode,'status':self.status,'external_upload_enabled':self.mode=='amap' and self.config.allow_external_maps,
                'configured':self.mode=='mock' or (self.mode=='amap' and bool(self.config.amap_api_key) and self.config.allow_external_maps)}

    def location_problem(self):
        if self.location_fault:
            return '定位不可用或已停止，请恢复定位'
        if not self.location:
            return '尚未提供位置'
        age=time.monotonic()-self.location['received_mono']+self.location['input'].age_s
        if age>self.config.location_ttl_s:
            return '定位已过期，请等待新位置'
        if self.location['input'].accuracy_m>self.config.location_max_accuracy_m:
            return '定位精度不足，暂停具体转向'
        return None

    def clear_route(self):
        self.generation+=1
        self.route=None;self.active=False;self.user_paused=False
        self.step_index=0;self.progress=0;self.route_distance=None
        self.deviations=0;self.arrivals=0;self.last_match_version=-1

    async def bounded(self,operation):
        try:
            return await asyncio.wait_for(operation,self.config.map_timeout_s)
        except asyncio.TimeoutError:
            raise NavError('地图操作超时，已停止具体导航提示') from None

    async def set_mode(self,mode):
        if self.lock.locked():
            raise NavError('地图请求处理中，请稍后切换')
        self.mode=mode;self.provider=build_map(self.config,mode)
        self.location=None;self.location_fault=None;self.location_version+=1
        self.search_id=None;self.candidates=[];self.clear_route();self.status='idle';self.error=None

    async def position(self,value):
        if self.position_lock.locked():
            raise NavError('正在处理上一条定位，请等待新样本')
        async with self.position_lock:
            previous = self.location['input'] if self.location else None
            if (previous and value.source == previous.source
                    and value.captured_at <= previous.captured_at):
                # Replayed samples do not refresh the recorded age or route progress.
                return self.state()
            version = self.location_version
            if self.location_problem():
                self.deviations=0;self.arrivals=0
            received_mono=time.monotonic()
            try:
                if self.mode=='mock' and value.source!='mock':
                    raise NavError('模拟路线不接收真实或手动位置，请切换高德或使用模拟起点')
                if self.mode!='mock' and value.source=='mock':
                    raise NavError('模拟位置不能用于真实高德导航')
                if value.age_s>self.config.location_ttl_s:
                    raise NavError('定位样本已经过期，未采用')
                point=Point(longitude=value.longitude,latitude=value.latitude,coordinate_system=value.coordinate_system)
                point=await self.bounded(self.provider.to_map_point(point))
                if version!=self.location_version:
                    raise NavError('定位已停止或失效，本次结果被丢弃')
                self.location_version += 1
                self.location={'input':value,'point':point,'received_at':utc_now(),'received_mono':received_mono}
                self.location_fault=None;self.error=None
                if self.location_problem():
                    self.deviations=0;self.arrivals=0
                if value.source=='manual' and self.active:
                    self.active=False;self.status='preview'
                self.update_progress()
            except NavError as exc:
                if version == self.location_version and not self.location:
                    self.location_fault = 'unavailable'
                raise
        return self.state()

    async def search(self,query,region,page=1, *, preserve_on_failure=False):
        if self.lock.locked():
            raise NavError('已有地图操作，请稍后再试')
        async with self.lock:
            if not preserve_on_failure:
                self.clear_route();self.status='searching';self.candidates=[];self.search_id=None
            generation = self.generation
            try:
                if self.mode == 'amap':
                    candidates = (await self.bounded(self.provider.candidates(query, region, page=page)))[:20]
                else:
                    candidates = (await self.bounded(self.provider.candidates(query, region)))[:8]
                if generation != self.generation:
                    raise NavError('地点搜索已取消')
                if preserve_on_failure and not candidates:
                    return self.state()
                if preserve_on_failure:
                    self.clear_route()
                self.candidates = candidates
                self.search_id=uuid.uuid4().hex;self.used_search=False
                self.status='select_destination';self.error=None
            except NavError as exc:
                if not preserve_on_failure and generation == self.generation:
                    self.status='error';self.error=str(exc)
                raise
        return self.state()

    async def plan(self,search_id,candidate_id):
        if self.lock.locked():
            raise NavError('已有地图操作，请稍后再试')
        async with self.lock:
            generation=self.generation
            try:
                if search_id!=self.search_id or self.used_search:
                    raise NavError('候选已过期或已使用，请重新搜索并确认')
                destination=next((v for v in self.candidates if v.candidate_id==candidate_id),None)
                if destination is None:
                    raise NavError('请从当前搜索候选中明确选择目的地')
                problem=self.location_problem()
                if problem:
                    raise NavError(problem)
                self.status='planning'
                origin=self.location['point']
                route=await self.bounded(self.provider.route(origin,destination))
                if generation!=self.generation:
                    raise NavError('路线规划已取消')
                if self.location_problem():
                    raise NavError('规划期间定位已失效，请更新位置后重试')
                if route.source!=self.mode or any(p.coordinate_system!='GCJ02' for s in route.steps for p in s.geometry):
                    raise NavError('路线来源或坐标系不一致')
                start_gap = meters(origin, route.steps[0].geometry[0])
                end_gap = meters(destination.point, route.steps[-1].geometry[-1])
                if start_gap > 100:
                    raise NavError(
                        f'高德路线起点距手机或手动起点约 {start_gap:.0f} 米。'
                        '请到附近可通行的道路入口，等定位更新后重试。'
                    )
                if end_gap > 100:
                    raise NavError(
                        f'高德路线终点距所选地点约 {end_gap:.0f} 米。'
                        '请搜索并选择实际要到达的校门、楼门或其他入口。'
                    )
                self.clear_route()
                self.route=route;self.route_mono=time.monotonic();self.status='preview'
                self.error=None;self.used_search=True
            except NavError as exc:
                if generation == self.generation and not self.route:
                    self.status='error';self.error=str(exc)
                raise
        return self.state()

    def start(self):
        if self.lock.locked():
            raise NavError('地图处理中，请稍后开始')
        problem=self.location_problem()
        if problem:
            raise NavError(problem)
        if not self.route:
            raise NavError('请先搜索、确认目的地并规划路线')
        if self.location['input'].source=='manual':
            raise NavError('手动坐标只允许路线预览；动态导航需浏览器持续定位')
        if time.monotonic()-self.route_mono>self.config.route_max_age_s:
            raise NavError('路线已过期，请重新规划')
        if meters(self.location['point'],self.route.origin)>60 and self.progress==0:
            raise NavError('当前位置离起点过远，请重新规划')
        self.active=True;self.user_paused=False;self.error=None
        self.update_progress()
        return self.state()

    def pause(self):
        self.user_paused=True
        return self.state()

    def stop(self):
        self.clear_route();self.status='idle'
        return self.state()

    def fault(self,status):
        self.location_version+=1;self.location_fault=status;self.deviations=0;self.arrivals=0
        return self.state()

    def update_progress(self):
        if not self.active or not self.route or self.user_paused or self.location_problem():
            return
        if self.last_match_version==self.location_version:
            return
        self.last_match_version=self.location_version
        point=self.location['point']
        (distance,along,index),total=project(point,self.route,self.progress)
        self.route_distance=round(distance,1)
        if self.status=='off_route':
            return  # latched until explicit route planning
        if distance>self.config.off_route_m:
            self.deviations+=1;self.arrivals=0
            self.status='off_route' if self.deviations>=3 else 'checking_position'
            return
        self.deviations=0
        if self.status=='off_route':
            return  # requires explicit replan, never auto invent recovery directions
        if along>self.progress+100 or along<self.progress-30:
            self.status='checking_position';return
        self.progress=max(self.progress,along)
        self.step_index=max(self.step_index,index)
        self.status='navigating'
        near=meters(point,self.route.destination.point)<=15 and total-self.progress<=25
        self.arrivals=self.arrivals+1 if near else 0
        if self.arrivals>=2:
            self.status='near_destination'  # never promise correct entrance / safe arrival

    def state(self):
        now=time.monotonic();problem=self.location_problem()
        status=self.status
        instruction=None
        if self.active:
            if self.user_paused:
                status='paused'
            elif problem:
                status='location_unavailable'
            elif now-self.route_mono>self.config.route_max_age_s:
                status='route_expired'
            else:
                self.update_progress();status=self.status
                if status=='navigating':
                    instruction=self.route.steps[self.step_index].instruction
        messages={'idle':'请提供起点，搜索并确认目的地', 'preview':'路线已准备；手动位置仅供预览',
                  'select_destination':'请明确确认一个目的地候选', 'paused':'导航已暂停',
                  'location_unavailable':problem or '定位不可用', 'route_expired':'路线已过期，请重新规划',
                  'off_route':'持续偏离路线，请确认位置并重新规划', 'checking_position':'位置与路线不一致，暂不提供具体转向',
                  'near_destination':'接近目的地，请由用户确认正确入口；不能保证已到达',
                  'error':self.error or '路线不可用', 'searching':'正在搜索目的地', 'planning':'正在规划路线'}
        location=None
        if self.location:
            value=self.location['input']
            location={'point':self.location['point'].model_dump(),'original_coordinate_system':value.coordinate_system,
                'source':value.source,'simulated':value.source=='mock','accuracy_m':value.accuracy_m,
                'captured_at':value.captured_at.isoformat(),'received_at':self.location['received_at'],
                'age_s':round(max(0,now-self.location['received_mono']+value.age_s),1), 'status':'valid' if not problem else 'unavailable'}
        return {**self.summary(),'status':status,'message':messages.get(status,'正在按地图路线提供提示'),
            'location':location,'location_fault':self.location_fault,'error':self.error,'search_id':self.search_id,
            'candidates':[v.model_dump() for v in self.candidates],
            'route':self.route.model_dump() if self.route else None,'instruction':instruction,
            'step_index':self.step_index,'progress_m':round(self.progress,1),'distance_to_route_m':self.route_distance,
            'simulated':self.mode=='mock','limits':{'location_ttl_s':self.config.location_ttl_s,'max_accuracy_m':self.config.location_max_accuracy_m}}
