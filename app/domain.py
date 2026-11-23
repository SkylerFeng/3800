"""Internal host contracts; these are NOT an ESP32 wire protocol."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Protocol
from pydantic import BaseModel, Field, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SampleStatus(str, Enum):
    valid = 'valid'
    waiting = 'waiting'
    interference = 'interference'
    no_target = 'no_target'
    too_close = 'too_close'
    out_of_range = 'out_of_range'
    stale = 'stale'
    fault = 'fault'
    not_installed = 'not_installed'
    unconfigured = 'unconfigured'


@dataclass(frozen=True)
class Frame:
    frame_id: str
    content: bytes = field(repr=False)
    received_at: str
    received_mono: float
    width: int
    height: int
    source: str
    simulated: bool
    static: bool = True
    valid: bool = True

    def public(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ('content', 'received_mono')} | {'age_s': round(max(0, time.monotonic() - self.received_mono), 2)}


class DistanceInput(BaseModel):
    status: SampleStatus = SampleStatus.valid
    distance_m: float | None = Field(None, ge=0.02, le=20, allow_inf_nan=False)

    @model_validator(mode='after')
    def consistent(self):
        if (self.status == SampleStatus.valid) != (self.distance_m is not None):
            raise ValueError('仅 valid 状态必须携带有效距离；异常状态距离应为空')
        return self


@dataclass(frozen=True)
class DistanceSample:
    value: DistanceInput
    received_at: str
    received_mono: float
    source: str = 'mock'


@dataclass(frozen=True)
class ImuSample:
    status: SampleStatus
    received_at: str
    source: str
    orientation: tuple[float, float, float] | None = None
    units: str = 'degrees'
    coordinate_frame: str = 'unconfigured'


@dataclass(frozen=True)
class Telemetry:
    distance: DistanceSample
    imu: ImuSample
    health: dict[str, str]


@dataclass(frozen=True)
class AudioUtterance:
    recording_id: str
    content: bytes = field(repr=False)
    started_at: str
    ended_at: str
    sample_rate_hz: int
    bit_depth: int
    channels: int
    complete: bool
    encoding: str


class HapticRequest(BaseModel):
    duration_ms: int = Field(ge=0, le=1000)
    intensity: float = Field(ge=0, le=1)
    reason: str
    stop: bool = False


class HapticResult(BaseModel):
    status: str  # simulated/rejected/unconfigured/failed/unknown/confirmed
    message: str


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    accuracy_m: float
    source: str
    received_at: str
    received_mono: float
    coordinate_system: str
    valid: bool


@dataclass(frozen=True)
class Destination:
    candidate_id: str
    label: str
    location: Location


@dataclass(frozen=True)
class RouteStep:
    instruction: str
    distance_m: float


@dataclass(frozen=True)
class Route:
    route_id: str
    steps: tuple[RouteStep, ...]
    source: str
    received_at: str


class CameraSource(Protocol):
    async def snapshot(self) -> Frame: ...


class TelemetrySource(Protocol):
    async def read(self) -> Telemetry: ...


class AudioInputSource(Protocol):
    async def record(self) -> AudioUtterance: ...


class VisionProvider(Protocol):
    # Implementations must use nonblocking IO and cooperate with cancellation.
    async def answer(self, frame: Frame, question: str) -> str: ...


class ASRProvider(Protocol):
    async def transcribe(self, recording: AudioUtterance) -> str: ...


class TTSProvider(Protocol):
    async def synthesize(self, text: str) -> bytes | None: ...


class AudioOutput(Protocol):
    async def play(self, audio: bytes | None) -> dict: ...
    async def stop(self) -> None: ...


class HapticOutput(Protocol):
    async def send(self, request: HapticRequest) -> HapticResult: ...


class LocationProvider(Protocol):
    async def locate(self) -> Location: ...


class MapProvider(Protocol):
    async def candidates(self, query: str) -> list[Destination]: ...
    async def route(self, origin: Location, destination: Destination) -> Route: ...
