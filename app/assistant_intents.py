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
    '继续刚才的导航': '继续导航', '恢复刚才的导航': '恢复导航',
}
SPLIT = re.compile(r'[。；;！？!?，,]+|然后|接着|再(?=帮我|看看|告诉我|查一下|看|说|问|导航|测距)')
DISTANCE = re.compile(
    r'声纳|超声波|测距|距离读数|障碍(?:物)?(?:离|有多远|多远|距离|预警)|'
    r'(?:前方|前面)(?:距离|有多远|多远)|离(?:前方)?障碍(?:物)?'
)
ROUTE_STATUS = re.compile(r'(?:导航|路线).*(?:状态|进度|怎么样)|现在怎么走|还要走多远|还有多远到目的地|下一步怎么走|当前路段')
DEVICE = re.compile(r'(?:相机|摄像头|眼镜|声纳|传感器|设备|imu|遥测).*(?:在线|连接|状态|正常|工作|就绪)')
VISION = re.compile(r'画面|眼前|前面|前方|看到|看一下|看一看|看看|看清|识别|颜色|'
                    r'在哪|哪里|哪边|哪一侧|左边|右边|是什么|有什么|能看|写着|写了|读一下|杯子|场景|它还在')
VISUAL_REFERENCE = re.compile(r'它们?|那个(?:东西|物品|目标)|上一个(?:东西|物品|目标)')
VISUAL_TARGET = re.compile(
    r'(?:请问|请|帮我|看看|看一下|画面[里中]的|眼前的|刚才(?:问过|提到|说过)的|这个|那个|刚才的)*'
    r'([\u4e00-\u9fff]{1,16}?)'
    r'(?:的颜色是什么|是什么颜色|是什么样子|在哪里|在哪[边儿侧]?)'
    r'(?:呢|啊|吗)?[？?。!！\s]*'
)


def vision_target(question):
    match = VISUAL_TARGET.fullmatch(question.strip())
    if match:
        target = match[1]
        if not re.search(r'和|还是|以及|什么|哪里|哪些|有几', target) and target not in (
            '它', '它们', '东西', '物品', '目标', '上一个', '上一个东西', '上一个物品', '上一个目标',
            '这个', '那个', '前方', '前面', '眼前', '画面',
        ):
            return target


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
