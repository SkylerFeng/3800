# 辅助眼镜开源项目调研

## 范围

对照用户提供的项目清单、上游仓库和论文，分析 3800 可借鉴的功能和验证方法。本轮新增此调研记录。附件中的部署和开发建议作为参考资料。

## 建议优先级

1. **固定素材回放和分段计时。** OpenSQZ 提供录制会话、重新输入模型和批量评估的工具。3800 可以用合成或经同意准备的图片、语音、距离及模拟定位，复现视觉问答、高德候选选择与确认、定位过期和声纳无回波。记录相机收到帧、提问结束、识别完成、模型返回、首段声音开始播放等时间。同一素材可用于比较 Qwen 设置、Matcha/Piper 和不同网络。当前记录中的浏览器约 291 毫秒、ESP32 直连约 295 毫秒是帧间隔。测量摄像到显示的实际延迟，可用独立设备同时拍摄计时画面和浏览器预览。 [OpenSQZ 回放说明](https://github.com/OpenSQZ/OpenGlass/tree/HEAD/runtime/openglass_omni#recording-and-replay) · [OpenSQZ 论文](https://aclanthology.org/2026.acl-demo.82/)
2. **为语音助手统一处理提示优先级。** Soundscape 的短音频提示和主动查询当前位置的交互值得参考。3800 已能语音确认路线，并在状态变化时停止过时的转向播报。可进一步用同一套规则安排近障提示、导航和问答，让近障短提示及时播放、过期指令退出队列，用户可以问“我在哪”“下一步是什么”或说“重复一遍”。用路线播报途中出现近障、定位过期和反复进入阈值的场景验证。 [Microsoft Soundscape](https://github.com/microsoft/soundscape)
3. **确认实体振动后，再增加简单的触觉区分。** 先检查驱动就绪、指令反馈与实际振感，再验证用户能否区分两种有限时长的节奏。保留基于有效声纳测距的确定性规则，核验无回波、过期、故障和近障的不同表现。论文报告的 13 种触觉平均识别率 81.3% 来自特定设备和实验。论文同时报告了 VICON 路径实验和视频决策实验。 [LLM-Glasses](https://arxiv.org/abs/2503.16475)
4. **让助手记住当前任务及其数据时间。** 借鉴 CMU 的工作记忆，保存正在确认的目的地、所用图像和目标上次可见的时间。用户问“它现在在哪”时，旧结果只能用于理解指代，应重新取图确认。稳定的室内地标与方向还需要空间定位，CMU 原型使用 iOS ARKit 和空间锚点。 [CMU 室内导航](https://github.com/CMU-TBD/ar-od-llm-indoor-navigation)

## 其他参考及边界

- BasedHardware 的 OpenGlass 与 3800 使用同款 XIAO ESP32S3 Sense，可参考安装结构和供电。原仓库 README 已标明迁移至 Omi。 [BasedHardware/OpenGlass](https://github.com/BasedHardware/OpenGlass)
- Berkeley Iris 的 Python 与 Arduino 仓库可参考视觉、声纳和马达的分工。TiresIAs 的单句录音加单张图片可参考为可重复的问答演示流程。GitHub 未识别出 OpenSQZ 和 Berkeley Iris 的仓库级许可证，复用具体源码前需要核实授权。Devpost 页面在本次访问中返回 403，附件中的赛事演示性能数字未核实。 [Iris 视觉](https://github.com/the-akze/berkAI-python-cv-etc) · [Iris 硬件](https://github.com/the-akze/berkAI-hardware-arduino) · [TiresIAs](https://github.com/guillesanbri/tiresias)
- OpenSQZ 的本地推理部署以附近电脑或边缘主机为基础，可作为保留眼镜采集接口、替换推理运行位置的参考。Android 原生运行、锁屏语音、高德跳转后的返回体验需要分别在手机上验证。

## 核对与待验证

本轮核对了上游仓库、回放文档及触觉论文，并对照 3800 的工作记录和导航说明。后续验证项为回放可重复性、实际预览及应答延迟、手机播报冲突、实体振感和真实步行导航体验。
