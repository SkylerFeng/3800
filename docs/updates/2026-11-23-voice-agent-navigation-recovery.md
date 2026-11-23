# 语音 Agent 的高德调度和导航恢复

## 本轮目标

在一次对话中调度眼镜问答、测距、导航和高德 App 跳转。识别失败、地点误识别或地图搜索超时后，用户能继续选择或沿用原路线。

## 实施

- 借鉴 CMU 原型的任务记忆，Agent 保留当前候选、已选地点、路线和等待确认的操作。没听清时提示当前步骤。新地名需在现有任务中确认，搜索超时和空结果保留原进度。语音结束操作有效期为 30 秒，需说「确认结束」。
- 增加本地 SenseVoice 普通话识别，使用 sherpa-onnx 2.0.3 和 2025-09-09 INT8 权重。在本机启用并保留 Vosk 的配置途径。录音送给本地识别进程，语音合成沿用现有配置。
- 语音说「用高德导航去图书馆」，选中候选后说「确认出发」，浏览器尝试按高德官方 Android Intent 启动步行导航。高德使用目标的 GCJ02 坐标和手机起点。页面提供同一目标的手动打开入口与高德网页入口。跳转时暂存会话凭据，同一标签页返回后可在有效期内继续。
- 沿用有明确权限的 Agent 工具调度与中文指令规则，补充候选序号和高德名称的常见转写变化。借鉴 OpenSQZ 的固定输入回放，对比两种识别模型，并回归识别失败、错误地点、搜索超时和结束确认。

## 验证

- 125 项 pytest 通过。JavaScript 语法和 Git diff 空白检查通过。
- 本地录音「步行去图书馆」通过语音 API、SenseVoice、Agent 进入模拟候选，处理约 0.86 秒。随后送入静音，候选仍可继续。固定样本中 Vosk 将该句转写为「不行去图书馆」，SenseVoice 正确。另一份普通话样本的开头 Vosk 更完整，实际手机口音及噪声表现有待测量。
- Chrome 使用 Android 窗口和已配置的高德 Web 服务查询公开地点国家图书馆。验证了候选、误识别提示、选定目的地、Intent 和回退链接、刷新恢复会话及重复已选地点。测试程序提交表单，模拟音频播放器结束，并拦截外部页面跳转与定位上传。
- 校园网 HTTPS 已重新启动。待用 vivo 实测录音、Android 浏览器允许自动拉起的条件、实际步行导航和从高德切回浏览器后的收音恢复。浏览器可能要求用户点击才能打开 App，页面会提示并保留既有任务。

## 参考

- [CMU 室内导航](https://github.com/CMU-TBD/ar-od-llm-indoor-navigation) · [OpenSQZ 会话回放](https://github.com/OpenSQZ/OpenGlass/tree/HEAD/runtime/openglass_omni#recording-and-replay)
- [SenseVoice 模型](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html) · [高德 Android 步行导航](https://lbs.amap.com/api/amap-mobile/guide/android/walk-navi) · [Chrome Android Intent](https://developer.chrome.com/docs/android/intents)
