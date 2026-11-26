import asyncio
import io
import time
import uuid
from PIL import Image, ImageDraw, ImageOps
from .domain import Frame, HapticRequest, HapticResult, utc_now


class NotConfigured(RuntimeError):
    pass


class LatestCamera:
    """One frame in memory; uploaded images are always static material."""
    def __init__(self):
        self._frame: Frame | None = None
        self.changed = asyncio.Event()
        self.mode = 'upload'
        self.message = '请上传图片或选择图像源'

    @property
    def frame(self) -> Frame | None:
        return self._frame

    @frame.setter
    def frame(self, value: Frame | None):
        self._frame = value
        self.changed.set()
        self.changed = asyncio.Event()

    def set_image(self, data: bytes, source: str, simulated: bool, static: bool = True):
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG', 'PNG'):
                raise ValueError('仅支持 JPEG / PNG')
            if image.width * image.height > 12_000_000:
                raise ValueError('图像不能超过1200万像素')
            image.load()
            clean = image.convert('RGB')
            if source in ('esp32', 'phone_snapshot'):
                clean = ImageOps.mirror(clean)
            out = io.BytesIO()
            clean.save(out, format='JPEG', quality=85)  # strip EXIF, including location
            frame = Frame(uuid.uuid4().hex, out.getvalue(), utc_now(), time.monotonic(), clean.width, clean.height, source, simulated, static)
        self.frame = frame
        self.mode = source
        return frame

    def mock(self):
        image = Image.new('RGB', (640, 360), '#e9edf3')
        draw = ImageDraw.Draw(image)
        draw.rectangle((400, 150, 490, 275), fill='#1e69d5')
        draw.ellipse((405, 140, 485, 162), fill='#398af0')
        draw.text((24, 24), 'SIMULATED STATIC SCENE - NOT A CAMERA', fill='#152944')
        draw.text((390, 295), 'illustrated cup', fill='#152944')
        out = io.BytesIO()
        image.save(out, format='PNG')
        return self.set_image(out.getvalue(), 'mock', True)

    async def snapshot(self):
        if self.frame is None:
            raise NotConfigured(self.message)
        return self.frame


class MockVisionProvider:
    async def answer(self, frame: Frame, question: str) -> str:
        await asyncio.sleep(.05)
        if frame.simulated:
            if '颜色' in question:
                example = '内置示意图中的杯子是蓝色。'
            elif any(word in question for word in ('哪里', '哪边', '方位', '位置')):
                example = '内置示意图中的杯子在画面右侧。'
            else:
                example = '内置示意图的右侧有一个蓝色杯子。'
            return '模拟回答，未执行真实图像识别。' + example
        return '模拟回答，未执行真实图像识别。你可选择内置示意图体验预设问答。'


class UnconfiguredVisionProvider:
    async def answer(self, frame: Frame, question: str) -> str:
        raise NotConfigured('未选择或未配置真实视觉 provider；未上传图片到外部服务。')


class MockTTSProvider:
    async def synthesize(self, text: str) -> None:
        await asyncio.sleep(0)
        return None


class MockAudioOutput:
    async def play(self, audio: bytes | None) -> dict:
        return {'status': 'simulated', 'message': '模拟播音事件；未合成音频，未播放声音'}

    async def stop(self):
        await asyncio.sleep(0)


class MockHapticOutput:
    async def send(self, request: HapticRequest) -> HapticResult:
        return HapticResult(status='simulated', message='仅主机模拟请求；真实马达未连接、未确认执行')


class UnconfiguredHapticOutput:
    async def send(self, request: HapticRequest) -> HapticResult:
        return HapticResult(status='unconfigured', message='触觉输出未配置；没有发送设备指令')
