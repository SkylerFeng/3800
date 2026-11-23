import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit
import ipaddress


@dataclass(frozen=True)
class Settings:
    camera_source: str = 'mock'
    telemetry_source: str = 'mock'
    vision_provider: str = 'mock'
    vision_api_base_url: str = ''
    vision_api_key: str = field(default='', repr=False)
    vision_api_model: str = ''
    allow_external_vision: bool = False
    ollama_base_url: str = 'http://127.0.0.1:11434'
    ollama_model: str = ''
    map_provider: str = 'mock'
    amap_api_key: str = field(default='', repr=False)
    allow_external_maps: bool = False
    assistant_session_s: float = 900
    map_timeout_s: float = 10
    location_ttl_s: float = 15
    location_max_accuracy_m: float = 30
    off_route_m: float = 35
    route_max_age_s: float = 1800
    tts_provider: str = 'mock'
    haptic_output: str = 'mock'
    esp32_host: str = ''
    esp32_camera_port: int = 80
    esp32_tcp_port: int = 9000
    camera_fps: float = 10
    device_timeout_s: float = 5
    device_retry_s: float = 2
    imu_ttl_s: float = 2
    haptic_ack_s: float = .6
    warning_pulse_ms: int = 150
    critical_pulse_ms: int = 300
    warning_intensity: float = .5
    critical_intensity: float = 1.0
    asr_provider: str = 'unconfigured'
    vosk_model_path: str = ''
    asr_timeout_s: float = 20
    recording_max_s: float = 22
    recording_ttl_s: float = 90
    tts_binary: str = 'espeak-ng'
    tts_voice: str = 'cmn'
    tts_speed: int = 155
    tts_timeout_s: float = 6
    matcha_model_dir: str = ''
    matcha_vocoder: str = ''
    matcha_speed: float = .95
    piper_model_path: str = ''
    piper_length_scale: float = 1.10
    piper_sentence_pause_s: float = .18
    sonar_min_m: float = .02
    sonar_max_m: float = 20.0
    model_timeout_s: float = 5
    distance_ttl_s: float = 3
    frame_ttl_s: float = 10
    critical_m: float = .5
    warning_m: float = 1.2
    hysteresis_m: float = .1
    haptic_interval_s: float = 1
    upload_max_bytes: int = 5 * 1024 * 1024

    def __post_init__(self):
        for name, options in {'map_provider': ('mock', 'amap', 'unconfigured'), 'camera_source': ('mock', 'upload', 'esp32'), 'telemetry_source': ('mock', 'esp32'), 'vision_provider': ('mock', 'unconfigured', 'openai_compatible', 'ollama'), 'tts_provider': ('mock', 'unconfigured', 'espeak', 'piper', 'matcha'), 'haptic_output': ('mock', 'unconfigured', 'esp32'), 'asr_provider': ('mock', 'unconfigured', 'vosk')}.items():
            if getattr(self, name) not in options:
                raise ValueError(f'{name}: 不支持的配置')
        import math
        for name in ('assistant_session_s', 'map_timeout_s', 'location_ttl_s', 'location_max_accuracy_m', 'off_route_m', 'route_max_age_s', 'model_timeout_s', 'distance_ttl_s', 'frame_ttl_s', 'critical_m', 'warning_m', 'hysteresis_m', 'haptic_interval_s', 'upload_max_bytes'):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} 必须为有限正数')
        for name in ('vision_api_base_url', 'ollama_base_url'):
            value = getattr(self, name)
            if not value:
                continue
            try:
                url = urlsplit(value)
                port = url.port
                valid = (url.scheme in ('http', 'https') and url.hostname
                         and not any(c.isspace() for c in value)
                         and not url.username and not url.password and not url.query and not url.fragment)
                try:
                    local = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname).is_loopback
                except ValueError:
                    local = False
                if not valid or (name == 'ollama_base_url' and not local) or (url.scheme == 'http' and not local):
                    raise ValueError()
            except ValueError:
                raise ValueError(f'{name} 地址无效；外部服务需 HTTPS，Ollama 仅允许本机地址，不能包含密钥或查询参数') from None
        if self.critical_m >= self.warning_m:
            raise ValueError('critical_m 必须小于 warning_m')
        if not .02 <= self.sonar_min_m < self.sonar_max_m <= 20:
            raise ValueError('声纳有效读数范围必须在 0.02 到 20 米之间')
        for name in ('device_timeout_s', 'device_retry_s', 'imu_ttl_s', 'haptic_ack_s',
                     'recording_ttl_s', 'asr_timeout_s', 'tts_timeout_s'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} 必须为有限正数')
        for name, value, low, high in (
            ('camera_fps', self.camera_fps, .25, 20),
            ('esp32_camera_port', self.esp32_camera_port, 1, 65535),
            ('esp32_tcp_port', self.esp32_tcp_port, 1, 65535),
            ('warning_pulse_ms', self.warning_pulse_ms, 1, 1000),
            ('critical_pulse_ms', self.critical_pulse_ms, 1, 1000),
            ('warning_intensity', self.warning_intensity, .01, 1),
            ('critical_intensity', self.critical_intensity, .01, 1),
            ('recording_max_s', self.recording_max_s, 1, 60),
            ('tts_speed', self.tts_speed, 80, 300),
            ('matcha_speed', self.matcha_speed, .65, 1.5),
            ('piper_length_scale', self.piper_length_scale, .65, 2),
            ('piper_sentence_pause_s', self.piper_sentence_pause_s, 0, 1),
        ):
            if not low <= value <= high:
                raise ValueError(f'{name} 超出配置范围')
        if self.esp32_host and not all(
            c.isascii() and (c.isalnum() or c in '.-') for c in self.esp32_host
        ):
            raise ValueError('ESP32_HOST 应填写设备 IPv4 地址或主机名')

    @classmethod
    def from_env(cls):
        defaults = cls()
        values = {}
        for name in cls.__dataclass_fields__:
            raw = os.environ.get(name.upper())
            if raw is None:
                continue
            default = getattr(defaults, name)
            if isinstance(default, bool):
                if raw.lower() not in ('true', 'false', '1', '0'):
                    raise ValueError(f'{name} 必须为 true / false 或 1 / 0')
                values[name] = raw.lower() in ('true', '1')
            else:
                values[name] = type(default)(raw)
        return cls(**values)
