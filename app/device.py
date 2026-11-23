"""XIAO wearable v3, verified against TransportProtocol.h and SensorManager.cpp."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json
import math
import struct
import time
import zlib

from .domain import DistanceInput, DistanceSample, HapticResult, utc_now

HEADER = struct.Struct('<4sBBHIQII')
IMU = struct.Struct('<7f')
DISTANCE = struct.Struct('<HHBBH')
STATUS = struct.Struct('<IIbBH')
PCM = struct.Struct('<IIIHBB')
MAX_PAYLOAD = 65536
MORE, ERROR, START, END = 1, 2, 4, 8


class WireError(ValueError):
    pass


async def read_frame(reader):
    magic, version, kind, flags, sequence, stamp, size, crc = HEADER.unpack(
        await reader.readexactly(HEADER.size)
    )
    if magic != b'XIAO' or version != 3 or size > MAX_PAYLOAD:
        raise WireError('设备协议头错误，预期 XIAO v3')
    payload = await reader.readexactly(size)
    if zlib.crc32(payload) != crc:
        raise WireError('设备数据 CRC 校验失败')
    return kind, flags, sequence, stamp, payload


def pack_frame(kind, payload, sequence=0, flags=0, stamp=0):
    return HEADER.pack(b'XIAO', 3, kind, flags, sequence, stamp, len(payload),
                       zlib.crc32(payload)) + payload


@dataclass(frozen=True)
class Recording:
    stream_id: int
    pcm: bytes
    sample_rate: int
    received_mono: float


class Recordings:
    def __init__(self, limit_s):
        self.limit_s = limit_s
        self.current = None
        self.latest = None
        self.problem = None

    def abort(self, message):
        self.current = None
        self.problem = message

    def feed(self, flags, payload):
        if len(payload) < PCM.size:
            self.abort('录音头不完整')
            return
        stream_id, index, rate, count, channels, bits = PCM.unpack_from(payload)
        data = payload[PCM.size:]
        if (flags not in (START | MORE, MORE, END) or rate != 16000
                or channels != 1 or bits != 16 or len(data) != count * 2
                or bool(count) == bool(flags & END)):
            self.abort('录音分片格式异常')
            return
        if flags & START:
            self.latest = None
            self.abort('录音起始索引异常' if index else None)
            if index:
                return
            self.current = (stream_id, 0, bytearray(), time.monotonic())
        if not self.current:
            self.problem = '未收到完整的录音起始标记'
            return
        expected_id, expected_index, buffer, began = self.current
        if (stream_id != expected_id or index != expected_index
                or len(buffer) + len(data) > rate * 2 * self.limit_s):
            self.abort('录音中断、缺少分片或超过时长限制')
            return
        if flags & END:
            self.latest = Recording(stream_id, bytes(buffer), rate, time.monotonic())
            self.current = None
        else:
            buffer.extend(data)
            self.current = (stream_id, index + 1, buffer, began)

    def expire(self, recording_ttl):
        now = time.monotonic()
        if self.current and now - self.current[3] > self.limit_s + 4:
            self.abort('等待录音结束超时')
        if self.latest and now - self.latest.received_mono > recording_ttl:
            self.latest = None

    def public(self):
        rec = self.latest
        return {'status': 'recording' if self.current else 'ready' if rec else 'idle',
                'error': self.problem, 'stream_id': rec.stream_id if rec else None,
                'duration_s': round(len(rec.pcm) / (2 * rec.sample_rate), 2) if rec else None}


class XIAO:
    def __init__(self, config):
        self.config = config
        self.status = 'unconfigured'
        self.message = '请设置 ESP32_HOST'
        self.writer = None
        self.sequence = 0
        self.command_lock = asyncio.Lock()
        self.ack = None
        self.imu = None
        self.health = None
        self.distance = None
        self.raw_distance = None
        self.recordings = Recordings(config.recording_max_s)

    async def run(self, enabled):
        while True:
            if not enabled():
                self.status, self.message = 'idle', '设备遥测未启用'
                await asyncio.sleep(.2)
                continue
            try:
                self.status, self.message = 'connecting', '正在等待设备数据'
                reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.config.esp32_host, self.config.esp32_tcp_port),
                    self.config.device_timeout_s,
                )
                while enabled():
                    packet = await asyncio.wait_for(read_frame(reader), self.config.device_timeout_s)
                    self.accept(*packet)
                    self.status, self.message = 'connected', '正在接收 XIAO v3 遥测'
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, WireError,
                    struct.error, UnicodeError, ValueError):
                self.status, self.message = 'disconnected', '设备连接中断或数据无效，正在重连'
            finally:
                writer, self.writer = self.writer, None
                self.imu = self.health = self.distance = self.raw_distance = None
                if self.recordings.current:
                    self.recordings.abort('设备断开，已丢弃未完成录音')
                if writer:
                    writer.close()
                    with suppress(OSError, asyncio.TimeoutError):
                        await asyncio.wait_for(writer.wait_closed(), .5)
            await asyncio.sleep(self.config.device_retry_s)

    def accept(self, kind, flags, sequence, stamp, payload):
        now = time.monotonic()
        if kind == 0x10:
            values = IMU.unpack(payload)
            norm = math.hypot(*values[:4])
            if all(map(math.isfinite, values)) and .5 < norm < 1.5 and not flags & ERROR:
                self.imu = now, stamp, tuple(v / norm for v in values[:4]), values[4:]
            else:
                self.imu = None
        elif kind == 0x11:
            mm, raw, code, unit, _ = DISTANCE.unpack(payload)
            status = {0: 'waiting', 1: 'valid', 2: 'no_target', 3: 'interference'}.get(code, 'fault')
            if flags & ERROR or unit not in (0, 1) or (status == 'valid' and mm in (0, 0xFFFF)):
                status = 'fault'
            elif status == 'valid':
                if mm / 1000 < self.config.sonar_min_m:
                    status = 'too_close'
                elif mm / 1000 > self.config.sonar_max_m:
                    status = 'out_of_range'
            value = DistanceInput(status=status, distance_m=mm / 1000 if status == 'valid' else None)
            self.distance = DistanceSample(value, utc_now(), now, 'esp32')
            self.raw_distance = {'raw': raw, 'raw_unit': 'mm' if unit == 0 else 'us', 'device_us': stamp}
        elif kind == 0x12:
            self.recordings.feed(flags, payload)
        elif kind == 0x13:
            uptime, heap, rssi, sensors, _ = STATUS.unpack(payload)
            self.health = now, {'uptime_ms': uptime, 'heap_bytes': heap, 'rssi_dbm': rssi,
                                **{name: bool(sensors & (1 << i)) for i, name in enumerate(
                                    ('imu_ready', 'sonar_ready', 'microphone_ready', 'motor_ready'))}}
        elif kind == 0x21 and self.ack:
            ack = json.loads(payload)
            seq, future = self.ack
            if isinstance(ack, dict) and ack.get('rx_sequence') == seq and not future.done():
                future.set_result(ack)

    def imu_state(self):
        if not self.imu:
            return {'status': 'waiting' if self.status == 'connected' else self.status}
        received, stamp, q, rpy = self.imu
        age = time.monotonic() - received
        if age > self.config.imu_ttl_s:
            return {'status': 'stale', 'age_s': round(age, 2)}
        return {'status': 'valid', 'age_s': round(age, 2), 'device_us': stamp,
                'quaternion_wxyz': q, 'rpy_deg': tuple(map(math.degrees, rpy))}

    def health_state(self):
        if self.health:
            received, values = self.health
            age = time.monotonic() - received
            if age <= self.config.device_timeout_s * 2:
                return {**values, 'age_s': round(age, 2)}
        return None

    async def command(self, name, args):
        if self.command_lock.locked() or self.writer is None or self.writer.is_closing():
            return None
        async with self.command_lock:
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF
            payload = json.dumps({'name': name, 'args': args}, separators=(',', ':')).encode()
            future = asyncio.get_running_loop().create_future()
            self.ack = self.sequence, future
            try:
                self.writer.write(pack_frame(0x20, payload, self.sequence,
                                             stamp=time.monotonic_ns() // 1000))
                await asyncio.wait_for(self.writer.drain(), self.config.haptic_ack_s)
                return await asyncio.wait_for(future, self.config.haptic_ack_s)
            except (OSError, asyncio.TimeoutError):
                return None
            finally:
                self.ack = None

    async def send(self, request):
        name = 'motor_stop' if request.stop else 'motor_pulse'
        args = {} if request.stop else {'strength': max(1, round(request.intensity * 127)),
                                        'duration_ms': request.duration_ms}
        ack = await self.command(name, args)
        if ack is None:
            return HapticResult(status='unknown', message='未收到匹配的设备 ACK')
        if (ack.get('status') == 'ok' and ack.get('motor_ready') is True
                and ack.get('motor_fault') is False
                and ack.get('motor_active') is (not request.stop)):
            return HapticResult(status='confirmed', message='固件报告驱动已执行，实际振感待现场确认')
        return HapticResult(status='failed', message='设备拒绝请求或驱动未报告预期状态')
