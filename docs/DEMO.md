# 课程演示

真机模式使用接在电脑同一局域网的 XIAO。离线模式提供有明确标识的模拟画面、模拟问答、距离和路线，用于复现交互和异常处理。

## 启动

在项目目录运行。真机配置填写在已有的本地 `.env`，保留 API 和地图设置。

```bash
.venv/bin/python scripts/serve.py --port 8001
```

打开 `http://127.0.0.1:8001/demo`。当前机器已配置 XIAO 地址和本地中文语音模型。换设备时查看串口报告的 IP 并替换 `ESP32_HOST`。固件 TCP 只允许一个客户端，运行 3800 时关闭占用 TCP 9000 的旧电脑端客户端。USB 串口监视独立于 TCP。

离线演示可同时启动，使用另一端口：

```bash
.venv/bin/python scripts/demo.py
```

打开 `http://127.0.0.1:8002/demo`。这个进程使用离线配置，装有 Piper 普通话模型时自动选用 Piper，也支持 eSpeak NG 播放。

## 3800 语音助手

手机连接同一热点，访问 `https://电脑热点IP:8443/assistant`。用 [导航 HTTPS 配置](NAVIGATION_SETUP.md) 启动服务。本机的热点 IP 为 `192.168.20.5` 时，直接打开 `https://192.168.20.5:8443/assistant`。

1. 做眼镜问答时，选择允许本次会话的提问和单张画面发给已配置的视觉服务。做真实导航时，勾选允许使用手机位置和高德地图，可以填写城市。
2. 点击「开启连续语音」，授予麦克风及所选的位置权限。保持页面在前台，等待助手提示正在听。
3. 说「杯子在哪里」，或等手机定位就绪后说「带我去图书馆」。听完回复再继续说。导航依次说「第二个」「确认出发」。也可说「下一组」「暂停导航」「继续导航」「重复上一条」「取消」或「结束助手」。

也可以说「先告诉我障碍有多远，再看看杯子在哪里」。助手会依次检查声纳和画面。说「摄像头在线吗」可以检查设备状态。

文字输入走同一套对话流程，可以检查识别出错后应当如何表达。离线演示支持文字流程和模拟路线。如果要使用麦克风，应配置 `ASR_PROVIDER=vosk` 和 `VOSK_MODEL_PATH`。语音播报使用当前本地 TTS 配置。会话默认最多 15 分钟，由 `ASSISTANT_SESSION_S` 调整。

## 三个演示场景

1. **物品问答。** 本轮用示例文本驱动。在控制台点击「颜色示例」或「方位示例」，编辑后提交。真机模式先让摄像头对准杯子。后续验证板载麦克风时，可短按 BOOT 开始，再短按结束，点击「识别眼镜录音」核对文字。云视觉开启时，对每次真机提问勾选发送许可。试听网页生成的答案，观察屏幕显示的实时快照编号。
2. **距离预警。** 把平整物体放在传感器正前方，从远向近移动，观察距离、提醒和紧急变化。可开启网页提示音。马达栏显示驱动是否可用以及请求结果。离线可点「正常距离」「近障碍」「数据过期」「传感器故障」复现状态。
3. **步行路线。** 进入导航页。离线选择内置起点并搜索「图书馆」，确认候选并开始，依次点击第一段中途、转角、第二段中途及终点附近。真机可使用手动公开坐标预览高德路线。动态引导使用手机位置，需可信 HTTPS。勾选路线自动播报或按「播放当前路段」。近距离预警、位置过期、路线偏离或暂停会抑制动态播报。

语音识别后的目的地可用「用输入内容找目的地」带到导航页，搜索和候选确认由用户完成。

## 本地语音配置

```bash
.venv/bin/python -m pip install -r requirements-voice.txt
mkdir -p models
curl -fL https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip -o models/vosk-cn.zip
unzip -o models/vosk-cn.zip -d models
sudo apt install espeak-ng
```

在 `.env` 中设置 `ASR_PROVIDER=vosk`、`VOSK_MODEL_PATH=models/vosk-model-small-cn-0.22`、`TTS_PROVIDER=espeak` 和 `TTS_BINARY=espeak-ng`。当前 Ubuntu 机器使用本地解包的 eSpeak NG 1.50，二进制位于 `artifacts/voice-tools/usr/bin/espeak-ng`，配合系统已安装的语音数据和共享库。语音通过本地子进程处理，录音和合成音频在主机内存中保留。完整录音默认 90 秒后释放。

Piper 普通话播报在本机合成，真机问答和导航共用配置。安装依赖和模型后在 `.env` 选择 `TTS_PROVIDER=piper` 并设置 `PIPER_MODEL_PATH=models/piper/zh_CN-huayan-medium.onnx`。

```bash
.venv/bin/python -m pip install -r requirements-tts.txt
mkdir -p models/piper
for file in zh_CN-huayan-medium.onnx zh_CN-huayan-medium.onnx.json; do
  curl -fL "https://huggingface.co/rhasspy/piper-voices/resolve/d5de91c972ba5a13cea0e77a63fda1623faac2c1/zh/zh_CN/huayan/medium/$file" -o "models/piper/$file"
done
```

这份 Huayan 模型用于本机演示，模型卡的训练数据许可标为 Unknown，项目未随仓库分发权重。语速用 `PIPER_LENGTH_SCALE` 调整，值越大越慢，句间停顿用 `PIPER_SENTENCE_PAUSE_S` 配置。

参考 [Vosk 模型目录](https://alphacephei.com/vosk/models) 和 [eSpeak NG](https://github.com/espeak-ng/espeak-ng)。

## 调参

| 功能 | 配置 |
| --- | --- |
| XIAO 地址与端口 | `ESP32_HOST`、`ESP32_CAMERA_PORT`、`ESP32_TCP_PORT` |
| 视频和遥测 | `CAMERA_SOURCE`、`TELEMETRY_SOURCE`、`CAMERA_FPS`、`DEVICE_TIMEOUT_S`、`DEVICE_RETRY_S` |
| 数据新鲜度 | `FRAME_TTL_S`、`DISTANCE_TTL_S`、`IMU_TTL_S` |
| 声纳读数范围 | `SONAR_MIN_M`、`SONAR_MAX_M` 控制主机采信的读数区间。实物量程按具体型号与现场测量确定 |
| 预警规则 | `WARNING_M`、`CRITICAL_M`、`HYSTERESIS_M` |
| 触觉 | `HAPTIC_OUTPUT`、`HAPTIC_INTERVAL_S`、`HAPTIC_ACK_S`、两个等级的 `*_PULSE_MS` 和 `*_INTENSITY` |
| 语音 | `ASR_PROVIDER`、`VOSK_MODEL_PATH`、`ASR_TIMEOUT_S`、录音上限和保留时间、`TTS_PROVIDER`、`TTS_BINARY`、`TTS_VOICE`、`TTS_SPEED`、`TTS_TIMEOUT_S` |
| 模型与导航 | `VISION_PROVIDER`、`MODEL_TIMEOUT_S`、`MAP_PROVIDER`、位置有效期、精度与偏离阈值 |

默认阈值为提醒 1.2 米和紧急 0.5 米，迟滞为 0.1 米。这些值用于受控演示，佩戴和环境标定需按实测调整。启动参数支持现有的 `--host`、`--port`、`--cert` 和 `--key`，环境变量优先于 `.env`。详细键名和默认值见 [.env.example](../.env.example)。


实时预览收到新帧后立即更新，`CAMERA_FPS` 限制主机处理帧率。实际更新受 ESP32 输出和网络影响。无有效回波时网页保留空距离并显示原因。标定声纳时，用平整目标垂直朝向探头，逐步拉远并记下最后稳定输出的距离，再配置读数上限。

Piper 语音来源见 [Piper](https://github.com/OHF-Voice/piper1-gpl) 和 [Huayan 模型卡](https://huggingface.co/rhasspy/piper-voices/blob/d5de91c972ba5a13cea0e77a63fda1623faac2c1/zh/zh_CN/huayan/medium/MODEL_CARD)。

## Matcha 普通话音色

本机真机和手机网页服务使用 Matcha 中文模型及 Vocos 声码器。语速由 `MATCHA_SPEED` 控制。可将 `TTS_PROVIDER` 改回 `piper` 试听原音色。对比音频保存在本机 `artifacts/matcha-navigation.wav` 和 `artifacts/piper-navigation.wav`。

在另一台主机上，先安装 `requirements-matcha.txt`，下载并解压模型，再按 `.env.example` 配置 `TTS_PROVIDER=matcha`、`MATCHA_MODEL_DIR`、`MATCHA_VOCODER` 和 `MATCHA_SPEED`。

```bash
.venv/bin/python -m pip install -r requirements-matcha.txt
mkdir -p models/matcha
curl -fL https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/matcha-icefall-zh-baker.tar.bz2 -o models/matcha/baker.tar.bz2
tar xjf models/matcha/baker.tar.bz2 -C models/matcha
curl -fL https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos-22khz-univ.onnx -o models/matcha/vocos-22khz-univ.onnx
```

模型训练数据限定非商业用途，本项目用于课程演示。模型说明见 [Sherpa-ONNX Matcha 文档](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/matcha.html)。
