"""Bounded multipart JPEG reader for the firmware's CameraService."""
import asyncio
from contextlib import suppress
from dataclasses import replace
import time

from PIL import Image

from .domain import utc_now
from .providers import LatestCamera


async def mjpeg(reader, limit):
    status = await reader.readline()
    if not status.startswith((b'HTTP/1.1 200 ', b'HTTP/1.0 200 ')):
        raise ValueError('相机未返回 HTTP 200')
    headers = await read_headers(reader)
    media, _, parameters = headers.get(b'content-type', b'').partition(b';')
    options = dict(part.strip().partition(b'=')[::2] for part in parameters.split(b';'))
    boundary = options.get(b'boundary', b'').strip(b'"')
    if media.strip().lower() != b'multipart/x-mixed-replace' or not 1 <= len(boundary) <= 70:
        raise ValueError('相机 MJPEG 边界无效')
    encoding = headers.get(b'transfer-encoding', b'identity').lower()
    if encoding == b'chunked':
        reader = ChunkedReader(reader)
    elif encoding != b'identity':
        raise ValueError('相机 HTTP 编码不受支持')
    delimiter = b'--' + boundary
    while True:
        line = await reader.readline()
        if line in (b'\r\n', b'\n'):
            line = await reader.readline()
        if line.rstrip(b'\r\n') != delimiter:
            raise ValueError('相机帧边界无效')
        headers = await read_headers(reader)
        length = int(headers.get(b'content-length', b'0'))
        if not 0 < length <= limit or headers.get(b'content-type', b'').lower() != b'image/jpeg':
            raise ValueError('相机图片格式或大小超出限制')
        yield await reader.readexactly(length)


async def read_headers(reader):
    headers = {}
    for _ in range(32):
        line = await reader.readline()
        if line == b'\r\n':
            return headers
        name, sep, value = line.partition(b':')
        if not sep:
            break
        headers[name.lower()] = value.strip()
    raise ValueError('相机 HTTP 头无效')


class ChunkedReader:
    """Remove HTTP chunk framing before parsing the multipart body."""
    def __init__(self, reader):
        self.reader = reader
        self.remaining = 0

    async def readexactly(self, size):
        result = bytearray()
        while len(result) < size:
            if not self.remaining:
                line = await self.reader.readline()
                self.remaining = int(line.split(b';', 1)[0].strip(), 16)
                if not 0 < self.remaining <= 8 * 1024 * 1024:
                    raise ValueError('相机 HTTP 分片结束或长度无效')
            step = min(size - len(result), self.remaining)
            result.extend(await self.reader.readexactly(step))
            self.remaining -= step
            if not self.remaining and await self.reader.readexactly(2) != b'\r\n':
                raise ValueError('相机 HTTP 分片结束符无效')
        return bytes(result)

    async def readline(self):
        result = bytearray()
        while len(result) < 2048:
            result.extend(await self.readexactly(1))
            if result.endswith(b'\n'):
                return bytes(result)
        raise ValueError('相机头过长')


async def run_camera(config, camera):
    while True:
        if camera.mode != 'esp32':
            await asyncio.sleep(.2)
            continue
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(
                config.esp32_host, config.esp32_camera_port, limit=8192), config.device_timeout_s)
            request = f'GET /stream HTTP/1.1\r\nHost: {config.esp32_host}\r\nConnection: close\r\n\r\n'
            writer.write(request.encode('ascii'))
            await asyncio.wait_for(writer.drain(), config.device_timeout_s)
            frames = mjpeg(reader, config.upload_max_bytes)
            camera.message = '等待实时帧'
            next_decode = 0
            while camera.mode == 'esp32':
                content = await asyncio.wait_for(anext(frames), config.device_timeout_s)
                now = time.monotonic()
                if now < next_decode:
                    continue
                received_at = utc_now()
                frame = await asyncio.to_thread(LatestCamera().set_image, content, 'esp32', False, False)
                if camera.mode == 'esp32':
                    camera.frame = replace(frame, received_at=received_at, received_mono=now)
                    camera.message = 'ESP32 实时画面'
                next_decode = now + 1 / config.camera_fps
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError,
                Image.DecompressionBombError, StopAsyncIteration):
            if camera.mode == 'esp32':
                camera.frame = None
                camera.message = '实时画面暂不可用，正在重连'
        finally:
            if writer:
                writer.close()
                with suppress(OSError, asyncio.TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), .5)
        await asyncio.sleep(config.device_retry_s)
