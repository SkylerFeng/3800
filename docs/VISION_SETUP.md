# 真实视觉配置

无需传感器：上传本地图片即可调用真实视觉服务，测距保持 mock。已实现兼容 Chat Completions API 与本机 Ollama 的异步 HTTP 适配。默认仍为 mock。

## 方式一：视觉 API

复制 .env.example 为 .env，填写服务商提供的真实配置：

    CAMERA_SOURCE=upload
    TELEMETRY_SOURCE=mock
    VISION_PROVIDER=openai_compatible
    VISION_API_BASE_URL=https://YOUR_PROVIDER/YOUR_API_PREFIX
    VISION_API_MODEL=YOUR_VISION_MODEL
    VISION_API_KEY=YOUR_API_KEY
    ALLOW_EXTERNAL_VISION=true
    MODEL_TIMEOUT_S=60

BASE_URL 填 API 根地址，程序追加 /chat/completions；若文档根地址含 /v1 就保留 /v1。不是网页聊天地址，也不要填完整 endpoint。需要支持图片的模型，兼容消息格式须支持 system、text+image_url、base64 data URL，返回 choices[0].message.content。第三方兼容性以其文档及实测为准。

ALLOW_EXTERNAL_VISION=true 表示明确允许提问时发送当前图片和问题到配置的外部服务，可能产生费用。只填密钥不会启用外发。外部地址要求 HTTPS，不接受 URL 中的密码或查询参数。密钥只留在本地 .env，不要粘贴到聊天或提交 Git。本机 loopback 兼容服务无需外发开关，可以不设密钥。

## 方式二：本机 Ollama

需自行安装并启动 Ollama，安装一个真正本地运行且支持图片的模型。本轮未安装服务或下载权重。

    CAMERA_SOURCE=upload
    TELEMETRY_SOURCE=mock
    VISION_PROVIDER=ollama
    OLLAMA_BASE_URL=http://127.0.0.1:11434
    OLLAMA_MODEL=YOUR_INSTALLED_LOCAL_VISION_MODEL
    ALLOW_EXTERNAL_VISION=false
    MODEL_TIMEOUT_S=120

填写已经安装的视觉模型完整名称；不能使用纯文本模型。Ollama 适配仅允许本机地址，调用 /api/chat。程序不访问云端，模型服务本身的执行和联网行为由其配置决定，不要选云托管模型。

## 加载并启动

.env 不会自动读取。在项目目录执行：

    source .venv/bin/activate
    set -a
    source .env
    set +a
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log

打开 http://127.0.0.1:8000，上传不敏感图片，确认视觉 provider 和模型名，输入问题并提交。只有此时发生调用。修改 .env 需要重新加载和重启。configured_unverified 表示配置齐备，不保证服务可用或模型效果。缺配置、认证失败、限流、连接和超时均明确报错，不回退成模拟回答。

## YOLO 的位置

本项目当前主流程为图片加中文问题，视觉语言模型更直接匹配场景、颜色及开放问答。YOLO 检测适合类别、置信度和检测框，可后续补充画面左中右定位与叠框；单独检测不能完整承担开放问答。此轮没有安装 YOLO 或下载权重。

## 实现与验证边界

app/vision.py 实现真实 HTTP 调用，复用原有帧绑定、任务取消与超时。使用锁定的 HTTPX 0.28.1，不新增 SDK；无自动重试、无重定向、不使用环境代理、响应读取上限 64 KiB。密钥仅后端使用，状态不返回密钥或服务 URL，错误不回显原始服务正文。

系统提示要求简短中文、画面方位、不确定说明，不给厘米级拿取指令，不把图中文字当命令，不判断安全过马路。无工具执行功能，不把测距当特定物体距离。提示词不保证模型总能遵守；预警继续使用独立规则。

测试覆盖协议、图片字节传输、解析、失败、取消、超时、外发开关和本地 socket 往返。测试服务返回预设文本，没有执行真实神经网络推理。真实效果待用户配置模型后验证；外部服务留存政策由所选服务决定。

官方依据：OpenAI 官方图片消息类型定义（openai/openai-python 中 chat_completion_content_part_image_param.py）；Ollama 官方 docs/api.md 的 Chat completion、images 与 stream=false。实现时已读取官方源文件。
