import time
from .config import Settings
from .domain import DistanceSample


UNAVAILABLE = {
    'no_target': '未收到有效回波，目标可能超出量程或角度不合适',
    'out_of_range': '回报值超出配置的测距范围',
    'interference': '声纳报告回波干扰',
    'waiting': '等待声纳数据',
    'stale': '测距数据已过期',
    'fault': '测距数据或设备连接异常',
    'not_installed': '声纳未就绪',
    'unconfigured': '声纳未配置',
}

ALERT_LEVELS = ('critical', 'warning', 'notice')
MESSAGES = {
    'critical': '前方距离很近',
    'warning': '前方有近距离障碍',
    'notice': '前方有障碍',
    'normal': '当前测距未触发阈值；不代表道路安全',
}


class WarningRules:
    def __init__(self, settings: Settings):
        self.config = settings
        self.level = 'unknown'

    def evaluate(self, sample: DistanceSample, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        age = max(0, now - sample.received_mono)
        status = sample.value.status.value
        if status in ('valid', 'too_close', 'out_of_range', 'no_target', 'interference') and age > self.config.distance_ttl_s:
            status = 'stale'
        distance = sample.value.distance_m if status == 'valid' else None
        if status == 'too_close':
            self.level = 'critical'
            message = '读数低于有效下限，按紧急距离处理'
        elif status != 'valid':
            self.level = 'unknown'
            message = UNAVAILABLE[status] + '，不能据此判断前方安全'
        else:
            thresholds = (self.config.critical_m, self.config.warning_m, self.config.notice_m)
            previous = self.level
            self.level = 'normal'
            for level, threshold in zip(ALERT_LEVELS, thresholds):
                limit = threshold + (self.config.hysteresis_m if level == previous else 0)
                if distance <= limit:
                    self.level = level
                    break
            message = MESSAGES[self.level]
        return {'level': self.level, 'status': status, 'distance_m': distance, 'age_s': round(age, 2),
                'alert_remaining_s': max(0, self.config.distance_ttl_s - age) if self.level in ALERT_LEVELS else 0,
                'received_at': sample.received_at, 'source': sample.source, 'simulated': sample.source == 'mock',
                'message': message}
