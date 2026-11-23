"""Validated host navigation contracts. Coordinates are always explicitly tagged."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .domain import utc_now


class NavError(RuntimeError):
    """Only fixed, non-sensitive descriptions may cross the API boundary."""


class Point(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    coordinate_system: Literal['WGS84', 'GCJ02']

    def pair(self) -> str:
        return f'{self.longitude:.6f},{self.latitude:.6f}'


class PositionInput(Point):
    source: Literal['manual', 'browser', 'mock']
    accuracy_m: float = Field(gt=0, le=100000, allow_inf_nan=False)
    captured_at: datetime
    age_s: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)

    @field_validator('captured_at')
    @classmethod
    def zoned(cls, value):
        if value.tzinfo is None:
            raise ValueError('定位时间必须包含时区')
        return value

    @model_validator(mode='after')
    def browser_coordinates(self):
        if self.source == 'browser' and self.coordinate_system != 'WGS84':
            raise ValueError('浏览器 Geolocation 必须标记 WGS84')
        return self


class Candidate(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    address: str = Field(default='', max_length=500)
    point: Point
    source: Literal['amap', 'mock']


class NavStep(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000)
    distance_m: float = Field(ge=0, le=1e6, allow_inf_nan=False)
    geometry: list[Point] = Field(min_length=2, max_length=20000)
    action: str = Field(default='', max_length=100)
    road_name: str = Field(default='', max_length=200)
    walk_type: str = Field(default='', max_length=20)


class NavRoute(BaseModel):
    route_id: str
    source: Literal['amap', 'mock']
    coordinate_system: Literal['GCJ02'] = 'GCJ02'
    received_at: str = Field(default_factory=utc_now)
    origin: Point
    destination: Candidate
    distance_m: float = Field(ge=0, le=1e6, allow_inf_nan=False)
    steps: list[NavStep] = Field(min_length=1, max_length=1000)


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=80)
    region: str = Field(default='', max_length=40)
    page: int = Field(default=1, ge=1, le=5)

    @field_validator('query')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('目的地不能为空')
        return value.strip()


class PlanInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: str = Field(min_length=1, max_length=128)
    search_id: str = Field(min_length=1, max_length=64)


class MapMode(BaseModel):
    value: Literal['mock', 'amap', 'unconfigured']


class LocationFault(BaseModel):
    status: Literal['denied', 'unavailable', 'stopped', 'timeout']
