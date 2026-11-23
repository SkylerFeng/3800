# AIAA 3800 · 视障辅助智能交互眼镜

电脑端软件连接 XIAO ESP32S3 Sense，提供实时画面、IMU 状态、A22 障碍预警、录音问答和步行导航。支持图片上传和离线模拟，图像、惯性数据、测距与语音各自显示来源和有效性。

- [演示步骤和调参](docs/DEMO.md)
- [项目目标、工作流程与验收](docs/PROJECT_WORKFLOW.md)
- [视觉服务配置](docs/VISION_SETUP.md)
- [手机定位与高德配置](docs/NAVIGATION_SETUP.md)

本机已配置真机。启动 `python scripts/serve.py` 后访问 `http://127.0.0.1:8001/demo`。用 `python scripts/demo.py` 启动另一个完全离线的演示实例，默认地址为 `http://127.0.0.1:8002/demo`。

## 安装和启动

实测 Ubuntu / Python 3.10.12 / Chrome。建议 Python 3.10+，单进程启动；Windows 尚未实测。

```bash
cd ~/Coding/3800
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

访问 <http://127.0.0.1:8000>，API 文档 <http://127.0.0.1:8000/docs>。无需复制配置即可启动。默认 mock 无外部请求；真实 provider 仅在提问时调用配置服务。安装需要网络或 wheel 缓存。不使用 `--workers`：状态、图片与任务均存于单进程内存。

若 Ubuntu 缺少 `ensurepip`，可安装系统的 `python3-venv`，或使用本次实际验证的替代方式：

```bash
python3 -m pip install --user virtualenv==20.36.1
python3 -m virtualenv .venv
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

直接运行 uvicorn 通过环境变量读取，不自动加载 `.env`；使用 `python scripts/serve.py` 会自动加载且默认监听 8001。Linux 也可显式加载自己编辑过的配置；已有 `.env` 时不要覆盖：

```bash
test -f .env || cp .env.example .env
set -a
source .env
set +a
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

PowerShell 可直接设 `$env:VISION_PROVIDER="unconfigured"` 等变量。完整选项见 [.env.example](.env.example)。无效配置会在启动时报错，不静默降级。真实视觉的 URL、模型、密钥和外发开关见 docs/VISION_SETUP.md。

## 演示步骤

1. 页面默认加载内置模拟静态图。上传 JPEG / PNG 后会显示“本地上传 · 静态素材”，而模型仍可独立保持 mock。图片仅存在内存，默认最多 5 MiB、1200 万像素；去除 EXIF 等元数据后重新编码。
2. 输入“杯子在哪里？”并提交。回答包含“模拟回答，未执行真实图像识别”；示例方位是固定脚本，不能作为对任何图片的判断。结果显示 `request_id`、`frame_id`、provider 和静态素材来源。
3. 按“正常距离”“近障碍”，观察 `normal` / `critical` 和 `simulated` 触觉事件。也可拖动滑块再按“发送当前距离”，或勾选持续发送。默认有效样本超过 3 秒会自然过期；关闭持续发送可验证。
4. 按“无目标”“数据过期”“传感器故障”“未安装”，等级应为 `unknown`，不能解释为安全。异常发生时发送模拟停止请求，既有危险状态不会被表述为“已安全”。
5. 各输入源、遥测和视觉模型可独立切换。真机画面需要配置 `ESP32_HOST`，外部视觉需要相应密钥和外发配置。
6. 快速重复提问会取消旧任务；“取消任务 / 播报”停止当前任务。模拟 TTS 产生演示事件，eSpeak 模式提供浏览器可播放的语音。

演示范围是浏览器与本机内存；不要将服务直接暴露公网。手机定位接收已实现，需要可信 HTTPS；真机外场尚未验证。

## 已实现与未实现

| 能力 | 当前交付 |
| --- | --- |
| FastAPI + 响应式中文调试网页 | 已实现，Chrome 实测 |
| 上传静态图片、内置模拟图像、最新帧缓存 | 已实现，单帧缓存，问题绑定不可变快照 |
| MockVisionProvider | 固定模拟回答；未识别图像 |
| 真实视觉 | 兼容 API / Ollama；云端真机画面按次确认后发送 |
| A22 和预警 | 毫米转米，确定性分级、过期和故障处理；可开启网页提示音 |
| 触觉 | 受限时长脉冲、停止和匹配 ACK；实机本轮报告驱动未就绪 |
| ESP32 画面和遥测 | 局域网 MJPEG / TCP v3，真机画面、IMU 和 A22 已测试 |
| 语音 | BOOT 录音分片接收、本地 Vosk 转写、eSpeak 中文 WAV 与浏览器播放 |
| 定位与地图 | 手机位置接收、手动预览、高德搜索/转换/步行路线、路线推进及播报；手机外场待实测 |

原始参考材料保存在 `docs/AIAA3800_*.md`。其中硬件优先级和历史完成状态属于参考文档内容；本轮实施以用户明确要求的软件先行范围为准。详见 [实现说明与硬件缺口](docs/IMPLEMENTATION.md)。

## 测试

```bash
source .venv/bin/activate
python -m pytest -q
python -m pip check
```

可选浏览器回归：使用独立的空模型配置服务，避免测试调用已配置的真实模型。测试会替换该服务的演示状态，图片由程序生成。以下命令在已激活虚拟环境的 Linux 终端执行：

```bash
VISION_PROVIDER=mock VISION_API_BASE_URL= VISION_API_MODEL= VISION_API_KEY= python -m uvicorn app.main:app --host 127.0.0.1 --port 8003 --no-access-log
# 另一个终端（同样激活虚拟环境）：
BROWSER_BASE_URL=http://127.0.0.1:8003 python tests/browser_smoke.py
```

默认寻找 `/usr/bin/google-chrome`；其他位置设 `CHROME_PATH`。Windows 可使用 `$env:CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"`。也可安装 Playwright Chromium 并将路径填入该变量。截图保存在忽略的 `artifacts/`，不含用户真实图片。

HTTP 启动检查：

```bash
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/state
```

测试结果和官方文档来源见 [验证记录](docs/VALIDATION.md)。

## API 与代码位置

| 接口 | 用途 |
| --- | --- |
| `GET /api/state` | 模块状态、数据年龄、任务、最近回答、40 条以内事件 |
| `GET /api/frame` | 最新图像（JPEG），`X-Frame-ID` 标识 |
| `POST /api/image` | 原始 JPEG / PNG 请求体，Content-Type 为 image/jpeg 或 image/png；不是 multipart |
| `POST /api/questions` | `{ "question": "杯子在哪里？", "frame_id": "当前帧ID" }`，立即返回 202，轮询 state 看结果 |
| `POST /api/cancel` | 取消当前问答及播报 |
| `POST /api/distance` | `{ "status": "valid", "distance_m": 0.3 }`；异常状态的 distance_m 必须为空或省略 |
| `POST /api/mode/{camera,telemetry,vision}` | `{ "value": "mock" }` 等独立切换 |

`app/domain.py` 定义契约；`providers.py` 为图片与模拟 provider；`warning.py` 为规则；`service.py` 负责任务、超时及有界事件；`main.py` 为 API；`app/web/` 是无构建依赖网页。

## 数据与可靠性

用户图像不落盘；默认不外发，明确启用外部视觉后提问会发送图片和问题；问题与回答只保存在内存中，关闭服务即清除。日志只记录请求 ID、帧 ID、耗时、状态和模拟事件，不记录图片、完整问题或 provider 异常原文。不要添加敏感内容到调试日志。生产 API Key 应使用环境变量且不得暴露给页面；真实视觉 provider 需要明确配置启用。

最多保留最新图片、当前任务快照、最近回答和 40 条事件，最多一个图像解码、一个视觉任务与一个播报任务；新任务替换旧任务而不排队。图像上传有字节、像素和 10 秒读取上限，图像解码在线程中进行；模型与播报按独立超时取消，默认不重试。规则每 100ms 检查数据过期；页面每 500ms 轮询。时间新鲜度使用主机 monotonic 时钟，不混用设备时间。静态素材显示接收年龄但不冒充实时画面；未来实时帧超过 `FRAME_TTL_S` 会拒绝问答。

扩展视觉 provider 必须使用异步客户端和连接/读取超时，配合取消；阻塞 SDK 不可直接在事件循环调用。取消线程不能终止同步网络调用，需另行设计有界 worker 或可终止进程后才接入。

## 故障排查

- `No module named ...`：确认使用 `.venv` 的 Python，重新运行 `python -m pip install -r requirements.txt`。
- 端口占用：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --no-access-log`，浏览器改访问 8001；浏览器脚本默认仍使用 8000。
- 无声音：配置 eSpeak NG，并在浏览器播放器点击播放。自动播放受浏览器权限限制。
- 测距变为 stale：单次样本默认只有效 3 秒；点击重新发送或启用持续模拟输入。
- 图片过大 / 格式错误：使用有效 JPEG / PNG，缩小至 1200 万像素以下、5 MiB 以下；后端不只依赖扩展名校验。
- 问题返回 409：图像缺失、无效、过期，或 frame_id 已变；刷新素材后重试。
- 真机连接失败：检查 `ESP32_HOST`、电脑与板子网络以及 TCP 9000 是否被旧客户端占用。视觉服务见 docs/VISION_SETUP.md。
- 模型失败 / timeout：网页显示任务错误，规则继续运行；自动测试注入失败与慢 provider 验证此行为。
- 浏览器测试找不到 Chrome：设置 `CHROME_PATH`，或安装本机 Chrome；普通运行和单元/API 测试不需要浏览器。


新版便捷启动：.venv/bin/python scripts/serve.py 自动加载本地 .env（环境变量优先），默认 8001；首页进入“定位与步行路线”。启动器支持 --cert/--key，详见导航说明。
