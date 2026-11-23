"""Local intent analysis for the 3800 assistant."""
from dataclasses import dataclass
import re
from typing import Literal
import unicodedata

Tool = Literal['vision', 'distance', 'device', 'route_status', 'navigation', 'amap', 'clarify']

DESTINATION = re.compile(r'(?:步行(?:导航)?(?:到|去)|导航(?:到|去)|带我(?:到|去)|我想去|我要去|去)(.+)')
NUMBER = re.compile(r'(?:我选|选择|就选|选)?第?([一二两三四五1-5])(?:个|项|号)?(?:吧)?')
AMAP_NAME = r'高[德得的](?:地图)?'
AMAP = re.compile(rf'(?:(?:打开|启动|调出|切换到){AMAP_NAME}(?:导航)?|'
                  rf'(?:用|在|交给){AMAP_NAME}(?:帮我)?(?:开始)?(?:步行)?导航)(.*)')
AMAP_SEARCH = re.compile(rf'(?:打开|启动|在|用){AMAP_NAME}(?:帮我)?(?:搜索|查找|查|搜)(.+)')
NAVIGATION_COMMANDS = frozenset((
    '结束助手', '退出助手', '关闭助手', '取消', '结束导航', '取消导航', '停止导航',
    '帮助', '你能做什么', '确认出发', '确认开始导航', '暂停导航', '暂停', '继续导航',
    '恢复导航', '重复上一条', '再说一遍', '重复导航', '下一组', '下一页', '更多候选',
    '确认到达', '我到了', '确认换目的地', '保留原路线', '取消换目的地', '重试搜索', '确认结束',
))
CONFIRMATION = frozenset(('确认出发', '确认开始导航', '确认换目的地', '确认结束'))
ALIASES = {
    '确认出发吧': '确认出发', '开始走吧': '确认出发', '开始导航吧': '确认开始导航',
    '再说一下': '再说一遍', '再播一遍': '再说一遍', '重复候选': '再说一遍',
}
SPLIT = re.compile(r'[。；;！？!?，,]+|然后|接着|再(?=帮我|看看|告诉我|查一下|看|说|问|导航|测距)')
DISTANCE = re.compile(
    r'声纳|超声波|测距|距离读数|障碍(?:物)?(?:离|有多远|多远|距离|预警)|'
    r'(?:前方|前面)(?:距离|有多远|多远)|离(?:前方)?障碍(?:物)?'
)
ROUTE_STATUS = re.compile(r'(?:导航|路线).*(?:状态|进度|怎么样)|现在怎么走|还要走多远|还有多远到目的地|下一步怎么走|当前路段')
DEVICE = re.compile(r'(?:相机|摄像头|眼镜|声纳|传感器|设备|imu|遥测).*(?:在线|连接|状态|正常|工作|就绪)')
VISION = re.compile(r'画面|眼前|前面|前方|看到|看一下|看一看|看看|看清|识别|颜色|'
                    r'哪里|哪边|哪一侧|左边|右边|是什么|有什么|能看|写着|写了|读一下|杯子|场景')


@dataclass(frozen=True)
class Action:
    tool: Tool
    text: str


def normalize(text):
    return re.sub(r'[\s，。！？、,.!?]', '', unicodedata.normalize('NFKC', text)).strip()


def classify(clause):
    text = re.sub(r'^(?:先|再(?!说|播)|请|帮我)+', '', normalize(clause))
    text = ALIASES.get(text, text)
    if AMAP_SEARCH.fullmatch(text) or AMAP.fullmatch(text):
        return Action('amap', text)
    if text in NAVIGATION_COMMANDS or NUMBER.fullmatch(text) or DESTINATION.fullmatch(text):
        return Action('navigation', text)
    if ROUTE_STATUS.search(text):
        return Action('route_status', clause)
    if DEVICE.search(text.lower()):
        return Action('device', clause)
    if DISTANCE.search(text):
        return Action('distance', clause)
    if VISION.search(text):
        return Action('vision', clause)
    return Action('clarify', clause)


def plan_actions(utterance):
    """Resolve tools in the spoken order. Route confirmation always gets its own turn."""
    clauses = [clause.strip() for clause in SPLIT.split(utterance) if normalize(clause)]
    if not clauses:
        return [Action('clarify', '')]
    actions = [classify(clause) for clause in clauses[:4]]
    if len(clauses) > 3:
        return [Action('clarify', '请一次说不超过三个请求。')]
    if len(actions) > 1 and any(
        action.tool == 'navigation' and action.text in CONFIRMATION for action in actions
    ):
        return [Action('clarify', '请先听完路线摘要，再单独说确认出发。')]
    return actions
