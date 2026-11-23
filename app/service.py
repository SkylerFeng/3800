import asyncio
from collections import deque
from contextlib import suppress
import json
import logging
import time
import uuid
from .config import Settings
from .device import XIAO
from .device_camera import run_camera
from .domain import DistanceInput, DistanceSample, HapticRequest, utc_now
from .providers import (LatestCamera,
                        MockTTSProvider, MockAudioOutput, MockHapticOutput, UnconfiguredHapticOutput)
from .navigation import Navigation
from .speech import LocalSpeech, SpeechError
from .warning import ALERT_LEVELS, WarningRules
from .vision import build_vision, vision_status, VisionError

logger = logging.getLogger('aiaa3800')


class Service:
    def __init__(self, config: Settings, vision=None):
        self.config = config
        self.device = XIAO(config)
        self.speech = LocalSpeech(config)
        self.device_tasks = []
        self.navigation = Navigation(config)
        self.camera = LatestCamera()
        if config.camera_source == 'mock':
            self.camera.mock()
        else:
            self.camera.mode = config.camera_source
            if self.camera.mode == 'esp32':
                self.camera.message = '等待实时帧' if config.esp32_host else '请设置 ESP32_HOST'
        self.vision_name = config.vision_provider
        self.vision = vision or build_vision(config, self.vision_name)
        self.tts = MockTTSProvider()
        self.audio = MockAudioOutput()
        self.haptic = MockHapticOutput() if config.haptic_output == 'mock' else UnconfiguredHapticOutput()
        self.telemetry_mode = config.telemetry_source
        self.sample = DistanceSample(DistanceInput(status='not_installed' if self.telemetry_mode == 'mock' else 'unconfigured'), utc_now(), time.monotonic(), self.telemetry_mode)
        self.rules = WarningRules(config)
        self.events = deque(maxlen=40)
        self.task = None
        self.audio_task = None
        self.audio_clip = None
        self.transcribing = False
        self.lock = asyncio.Lock()
        self.current = None
        self.last_answer = None
        self.last_error = None
        self.audio_event = {'status': config.tts_provider, 'message': '未播放声音'}
        self.last_haptic = None
        self.last_haptic_at = float('-inf')
        self.last_haptic_level = None
        self.real_pulse_possible = False
        self.monitor = None
        self.haptic_task = None

    @property
    def distance_sample(self):
        if self.telemetry_mode != 'esp32' or not self.config.esp32_host:
            return self.sample
        if self.device.distance is not None:
            return self.device.distance
        health = self.device.health_state()
        status = 'fault' if self.device.status == 'disconnected' else 'waiting'
        if health and not health['sonar_ready']:
            status = 'not_installed'
        return DistanceSample(DistanceInput(status=status), utc_now(), time.monotonic(), 'esp32')

    async def start(self):
        self.monitor = asyncio.create_task(self.watch_distance())
        if self.config.esp32_host:
            self.device_tasks = [
                asyncio.create_task(self.device.run(lambda: self.telemetry_mode == 'esp32')),
                asyncio.create_task(run_camera(self.config, self.camera)),
            ]

    async def close(self):
        tasks = (self.monitor, self.task, self.audio_task, self.haptic_task, *self.device_tasks)
        for task in tasks:
            if task:
                task.cancel()
        for task in tasks:
            if task:
                with suppress(asyncio.CancelledError):
                    await task
        await self.audio.stop()

    def event(self, kind: str, **fields):
        event = {'at': utc_now(), 'kind': kind, **fields}
        self.events.append(event)
        # No question, image, audio, coordinates, or credentials in logs.
        logger.info(json.dumps(event, ensure_ascii=False))

    async def watch_distance(self):
        while True:
            await self.evaluate_distance()
            self.device.recordings.expire(self.config.recording_ttl_s)
            if self.device.recordings.current and self.audio_clip:
                self.audio_clip = None
                self.audio_event = {'status': 'muted', 'message': '录音开始，已停止旧播报'}
            await asyncio.sleep(.1)

    async def evaluate_distance(self):
        warning = self.rules.evaluate(self.distance_sample)
        level = warning['level']
        now = time.monotonic()
        previous = self.last_haptic_level
        if self.haptic_task and not self.haptic_task.done():
            return warning
        self.last_haptic_level = level
        active = level in ALERT_LEVELS
        if active and (level != previous or now - self.last_haptic_at >= self.config.haptic_interval_s):
            critical = level == 'critical'
            request = HapticRequest(
                duration_ms=self.config.critical_pulse_ms if critical else self.config.warning_pulse_ms,
                intensity=self.config.critical_intensity if critical else self.config.warning_intensity,
                reason=level)
        elif previous in ALERT_LEVELS and not active:
            request = HapticRequest(duration_ms=0, intensity=0, reason=level, stop=True)
        else:
            return warning
        self.last_haptic_at = now
        if self.config.haptic_output == 'esp32':
            if request.stop and not self.real_pulse_possible:
                return warning
            if not request.stop and self.telemetry_mode != 'esp32':
                return warning
            health = self.device.health_state()
            if not request.stop and (not health or not health['motor_ready']):
                if level != previous or not self.last_haptic:
                    self.last_haptic = {'at': utc_now(), 'request': request.model_dump(),
                                        'result': {'status': 'unavailable',
                                                   'message': '设备未报告马达就绪'}}
            else:
                self.real_pulse_possible = not request.stop
                self.haptic_task = asyncio.create_task(self.deliver_haptic(self.device, request))
        else:
            await self.deliver_haptic(self.haptic, request)
        return warning

    async def deliver_haptic(self, output, request):
        result = await output.send(request)
        self.last_haptic = {'at': utc_now(), 'request': request.model_dump(), 'result': result.model_dump()}
        self.event('haptic', **self.last_haptic)

    async def distance(self, value: DistanceInput):
        if self.telemetry_mode != 'mock':
            raise ValueError('请切换到 mock 遥测后再输入模拟数据')
        self.sample = DistanceSample(value, utc_now(), time.monotonic())
        return await self.evaluate_distance()

    async def submit(self, question: str, frame_id: str | None, allow_live_upload=False):
        async with self.lock:
            frame = await self.camera.snapshot()
            if frame_id != frame.frame_id and not (frame_id is None and frame.source == 'esp32'):
                raise ValueError('图像已变更，请查看最新图片后重新提问')
            if not frame.valid:
                raise ValueError('图像无效，无法回答')
            if not frame.static and time.monotonic() - frame.received_mono > self.config.frame_ttl_s:
                raise ValueError('实时图像已过期，请更新画面后重试')
            if (frame.source == 'esp32' and vision_status(self.config, self.vision_name)['external_upload_enabled']
                    and not allow_live_upload):
                raise ValueError('请确认允许将当前一帧画面和问题发送给视觉服务')
            if self.task and not self.task.done():
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
                if self.current and self.current['status'] == 'running':
                    self.current.update(status='cancelled', error='已在执行前取消')
            if self.audio_task and not self.audio_task.done():
                self.audio_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.audio_task
            await self.audio.stop()
            self.audio_clip = None
            self.audio_event = {'status': 'idle', 'message': '新任务已停止旧播报'}
            self.last_error = None
            request_id = uuid.uuid4().hex
            self.current = {'request_id': request_id, 'frame_id': frame.frame_id, 'frame_source': frame.source, 'frame_static': frame.static, 'frame_received_at': frame.received_at, 'provider': self.vision_name, 'simulated': self.vision_name == 'mock', 'status': 'running', 'answer': None, 'error': None}
            self.task = asyncio.create_task(self.run(self.current, frame, question))
            return dict(self.current)

    async def run(self, record, frame, question):
        began = time.monotonic()
        request_id = record['request_id']
        self.event('task_started', request_id=request_id, frame_id=frame.frame_id, provider=self.vision_name)
        try:
            answer = await asyncio.wait_for(self.vision.answer(frame, question), self.config.model_timeout_s)
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 4000:
                raise ValueError('模型返回内容无效')
            record.update(status='completed', answer=answer)
            self.last_answer = dict(record)
            self.audio_task = asyncio.create_task(self.speak(answer, request_id))
        except asyncio.CancelledError:
            record.update(status='cancelled', error='已由新任务或取消操作终止')
        except asyncio.TimeoutError:
            record.update(status='timeout', error='模型响应超时，请重试；测距规则仍独立运行')
            self.last_error = record['error']
        except VisionError as exc:
            record.update(status='failed', error=str(exc))
            self.last_error = record['error']
        except Exception:
            # provider exception may contain credentials / full HTTP payloads: do not log it.
            record.update(status='failed', error='模型 provider 不可用或返回异常；请检查配置')
            self.last_error = record['error']
        finally:
            record['elapsed_s'] = round(time.monotonic() - began, 3)
            self.event('task_finished', request_id=request_id, status=record['status'], elapsed_s=record['elapsed_s'])

    async def speak(self, answer, request_id):
        try:
            if self.speech.tts_enabled:
                if self.device.recordings.current:
                    self.audio_event = {'status': 'muted', 'message': '录音期间已暂停播报'}
                    return
                audio = await self.speech.synthesize(answer[:1200])
                if self.device.recordings.current:
                    self.audio_event = {'status': 'muted', 'message': '录音期间已暂停播报'}
                    return
                self.audio_clip = (request_id, audio)
                self.audio_event = {'status': 'ready', 'message': '语音已生成，可在网页播放',
                                    'request_id': request_id}
                return
            if self.config.tts_provider != 'mock':
                self.audio_event = {'status': 'unconfigured', 'message': 'TTS / 电脑音频输出未配置，未播放声音', 'request_id': request_id}
                return
            audio = await asyncio.wait_for(self.tts.synthesize(answer), 2)
            self.audio_event = await asyncio.wait_for(self.audio.play(audio), 1) | {'request_id': request_id}
            self.event('audio', **self.audio_event)
        except asyncio.CancelledError:
            await self.audio.stop()
            raise
        except SpeechError as exc:
            self.audio_event = {'status': 'failed', 'message': str(exc), 'request_id': request_id}
        except Exception:
            self.audio_event = {'status': 'failed', 'message': '语音输出失败，未确认播放', 'request_id': request_id}

    async def cancel(self):
        async with self.lock:
            if self.task and not self.task.done():
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
                if self.current and self.current['status'] == 'running':
                    self.current.update(status='cancelled', error='已在执行前取消')
            if self.audio_task:
                self.audio_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.audio_task
            await self.audio.stop()
            self.audio_clip = None
            self.audio_event = {'status': 'cancelled', 'message': '播音任务已取消，未播放声音'}

    async def transcribe(self, stream_id):
        if self.transcribing:
            raise SpeechError('已有语音识别任务')
        recordings = self.device.recordings
        recordings.expire(self.config.recording_ttl_s)
        recording = recordings.latest
        if self.telemetry_mode != 'esp32' or not recording or recording.stream_id != stream_id:
            raise SpeechError('没有可识别的完整录音，请用 BOOT 键重新录音')
        self.transcribing = True
        try:
            text = await self.speech.transcribe(recording)
            if recordings.latest is not recording or self.telemetry_mode != 'esp32':
                raise SpeechError('录音已经切换，请选择最新录音')
            return {'text': text, 'simulated': self.config.asr_provider == 'mock'}
        finally:
            self.transcribing = False

    def navigation_state(self):
        result = self.navigation.state()
        obstacle = self.rules.evaluate(self.distance_sample)
        result['obstacle_priority'] = obstacle['level'] in ALERT_LEVELS
        if result['obstacle_priority']:
            result['instruction'] = None
            result['message'] = '近距离障碍提醒优先，导航转向提示暂缓'
        result['speech_enabled'] = self.speech.tts_enabled
        result['recording_active'] = bool(self.device.recordings.current)
        return result

    async def state(self):
        warning = await self.evaluate_distance()
        real = self.telemetry_mode == 'esp32'
        frame = self.camera.frame.public() if self.camera.frame else None
        camera_status = 'valid' if frame else 'unconfigured'
        if frame and not frame['static'] and frame['age_s'] > self.config.frame_ttl_s:
            camera_status = 'stale'
        health = self.device.health_state() if real else None
        haptic_status = {
            'mock': 'simulated', 'unconfigured': 'unconfigured',
            'esp32': ('ready' if health['motor_ready'] else 'driver_unavailable') if health else 'waiting',
        }[self.config.haptic_output]
        recording = self.device.recordings.public() if real else {'status': 'idle'}
        return {
            'camera': {'mode': self.camera.mode, 'status': camera_status,
                       'frame': frame, 'message': self.camera.message},
            'distance': warning | ((self.device.raw_distance or {}) if real else {}),
            'imu': self.device.imu_state() if real else {'status': 'unconfigured'},
            'haptic': {'status': haptic_status, 'last_event': self.last_haptic},
            'vision': vision_status(self.config, self.vision_name),
            'audio': self.audio_event | {'provider': self.config.tts_provider},
            'recording': recording | {'provider': self.config.asr_provider,
                                      'transcribing': self.transcribing},
            'telemetry': {'source': self.telemetry_mode, 'status': self.device.status if real else 'simulated',
                          'message': self.device.message if real else '模拟遥测', 'health': health},
            'location': {'status': 'valid' if not self.navigation.location_problem() else 'unavailable'},
            'map': self.navigation.summary(), 'current_task': self.current,
            'last_answer': self.last_answer, 'last_error': self.last_error, 'events': list(self.events),
            'thresholds': {'critical_m': self.config.critical_m, 'warning_m': self.config.warning_m,
                           'notice_m': self.config.notice_m,
                           'hysteresis_m': self.config.hysteresis_m, 'ttl_s': self.config.distance_ttl_s},
        }
