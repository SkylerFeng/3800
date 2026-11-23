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
            critical = self.config.critical_m
            warning = self.config.warning_m
            margin = self.config.hysteresis_m
            if self.level == 'critical' and distance <= critical + margin:
                self.level = 'critical'
            elif distance <= critical:
                self.level = 'critical'
            elif self.level in ('critical', 'warning') and distance <= warning + margin:
                self.level = 'warning'
            elif distance <= warning:
                self.level = 'warning'
            else:
                self.level = 'normal'
            message = {'critical': '前方距离很近', 'warning': '前方有近距离障碍', 'normal': '当前测距未触发阈值；不代表道路安全'}[self.level]
        return {'level': self.level, 'status': status, 'distance_m': distance, 'age_s': round(age, 2), 'received_at': sample.received_at, 'source': sample.source, 'simulated': sample.source == 'mock', 'message': message}
