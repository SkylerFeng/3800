# 定位与步行路线使用说明

已实现：高德搜索/坐标转换/步行路线适配、浏览器持续定位接收、手动起点预览、目的地候选确认、路线几何示意、导航步骤管理、暂停恢复、过期/精度/偏离降级。地图与视觉、遥测独立，导航不调用千问生成路线。

## 无密钥模拟演示

在项目目录执行：

    .venv/bin/python scripts/serve.py

默认自动加载 .env、监听本机 8001。Windows 使用 .venv\Scripts\python.exe。已有服务占用端口可先停止旧进程，或指定 --port 8002。访问 http://127.0.0.1:8001/navigation，也可从首页链接进入。

1. 选“模拟路线”，点击“使用内置模拟起点”。
2. 输入图书馆等任意词搜索；返回两个明确虚构的候选。
3. 确认其中一个，检查路线和两段步骤，再开始提示。
4. 顺序点击第一段中途、转角、第二段中途、终点附近；终点再点击一次，提示“接近目的地，请确认入口”。
5. 重新从起点规划，连续三次点击偏离位置，验证停止转向、要求重新规划。刷新不算新位置证据。
6. 不更新位置超过 15 秒，定位过期，具体提示停止。

模拟线路和地点为虚构，不能真实出行。模拟模式不接收真实浏览器位置，高德模式不接收模拟位置。切模式清空旧位置、候选和路线。

## 在手机高德中导航

在手机浏览器打开导航页，使用顶部「用手机高德导航」。输入地点，或点击「说出目的地」，允许浏览器使用麦克风，说完后点「结束并识别」。录音经 HTTPS 发送给本机 Vosk 识别。核对输入框中的地名，再点「在高德地图搜索」。在高德中选择正确入口，切到步行并开始导航。起点、道路底图、路线更新和播报由高德处理。浏览器阻止打开 App 时，可以在高德页面手动打开 App。

如果已经在项目中搜到了目的地，点该候选旁的「用高德步行导航」，即可把选中的终点交给高德，由高德获取手机当前位置。在项目预览出路线后，也可点「在高德地图查看步行路线」。高德会根据链接中的起终点重新计算路线。

项目内搜索每页最多 20 个地点，结果不足时可翻页，也可以填城市缩小范围。页面内定位和地图请求可并行处理。定位暂时没有更新时，项目沿用仍有效的上一条样本，过期后暂停转向。

「试听普通话」可独立于路线播放测试句。如果浏览器拦截自动播放，用页面音频播放器手动播放。项目内当前路段播报继续遵循位置和障碍状态。

## 配置高德

在本地 .env 添加下列内容，保留现有千问和其他配置：

    MAP_PROVIDER=amap
    AMAP_API_KEY=你的高德Web服务Key
    ALLOW_EXTERNAL_MAPS=true
    MAP_TIMEOUT_S=10
    LOCATION_TTL_S=15
    LOCATION_MAX_ACCURACY_M=30
    OFF_ROUTE_M=35
    ROUTE_MAX_AGE_S=1800

需要高德“Web 服务”类型 Key，不是千问或 JS API Key；权限、费用、配额和地区覆盖需根据高德账户确认。系统不购买或修改账户。明确启用后，搜索会发送搜索词/区域，规划发送端点/POI，浏览器 WGS84 位置提交会发至高德转换接口。密钥只在后端使用。缺少配置不会回退成模拟路线。

重启启动器。地图选高德，在手动起点中输入经纬度、坐标系和位置误差，搜索并确认具体入口，规划路线。高德坐标选择 GCJ02，GPS/浏览器坐标为 WGS84。手动坐标仅用于预览，不允许动态导航。

只绘制返回的路线几何和位置，不含高德道路底图，不需要前端 JS Key。页面提供高德地图跳转。App 自行维护导航进度。

## 手机持续定位和 HTTPS

高德模式点击“开始手机/浏览器定位”才申请权限。浏览器 WGS84 坐标由后端明确转换，不对 GCJ02 重复转换。最多约每 3 秒一个位置。位置包含精度、采集时间、接收时间、来源、坐标系；主机单调时钟加客户端缓存年龄判断过期。重复/乱序样本拒绝。保持浏览器前台，锁屏/后台不保证定位。

手机不能访问电脑的 127.0.0.1，必须同一可信局域网并访问电脑 IP。普通局域网 HTTP 通常不是安全上下文，需要手机信任的 HTTPS 证书：

    .venv/bin/python scripts/serve.py --host 0.0.0.0 --port 8443 --cert certs/server.crt --key certs/server.key

手机访问 https://电脑局域网IP:8443/navigation。证书 SAN 必须包含实际访问 IP/主机名，签发 CA 需手机信任。可由自己管理的本地 CA 工具准备；项目不自动生成/安装根证书、不修改手机。仅忽略证书警告不能视为可信上下文。certs/ 已 Git 忽略，不提交私钥。

此服务为单用户实验台，没有登录、设备配对或多用户隔离。导航 token 防跨站写入，不是用户认证；能访问页面的局域网用户也可能读取状态。绑定 0.0.0.0 仅用于自己控制的可信网络，不暴露公网。正式部署需认证和会话隔离。

电脑 localhost 也可尝试定位，但电脑精度可能不足；误差超阈值会阻止具体导航。

## 状态与边界

- 位置默认最多 15 秒、误差不超过 30m。采集时间带时区，但不直接拿设备时钟与主机相减。不能抵抗客户端伪造位置。
- 最近线段投影估计沿线进度，限制大幅跳段。不是成熟 SDK，平行路/路口/楼层等仍需现场验证。
- 连续三个独立样本偏离超过 35m 后暂停转向；用户明确重新搜索规划，不后台频繁重算。
- 两个样本且沿线路径支持时仅提示接近终点，需确认入口。
- 定位过期/差精度/停止、路线过期或服务失败，不输出具体动态转向；静态步骤仍可预览。
- 障碍 warning/critical 时压制导航指引和播报。配置本地语音模型后，可播放当前路段或开启路段更新时自动播报。
- 不判断安全过街，不声称路线无障碍或能检测坑洞、台阶。
- 最新位置、候选、路线只存在内存，不记轨迹。导航 API 因用途返回坐标；主状态摘要和日志不含坐标。
- 高德规定 Key 使用 query；代码过滤该服务的 HTTPX 请求 URL 日志，不回显原始错误正文。不要用其他调试代理记录敏感 URL。

## 代码与 API

app/nav_types.py 定义导航校验类型，app/maps.py 实现 RouteProvider 和适配器，app/navigation.py 是状态机，app/nav_api.py 提供 API，app/web/navigation.* 是页面。

domain.py 中早期 LocationProvider/MapProvider 是第一阶段契约；本轮实际导航采用更完整的 nav_types 和 RouteProvider，不把旧空 Protocol 当已实现。

先 GET /api/navigation/session 获取进程 token，其他导航接口带 X-Navigation-Token。

- GET /api/navigation/state：导航状态，含障碍优先处理。
- POST /api/navigation/mode：value=mock/amap/unconfigured。
- POST /api/navigation/position：longitude、latitude、coordinate_system、source、accuracy_m、captured_at、age_s。
- POST /api/navigation/location-fault：denied/unavailable/stopped/timeout。
- POST /api/navigation/search：query、可选 region。
- POST /api/navigation/plan：当前 search_id 和 candidate_id，明确确认后规划。
- POST /api/navigation/start、pause、stop。
- POST /api/navigation/mock-position：point=start/middle/corner/north/end/off_route，仅 mock。

测试命令：

    .venv/bin/python -m pytest -q
    BROWSER_BASE_URL=http://127.0.0.1:8001 .venv/bin/python tests/browser_navigation.py

浏览器脚本会替换导航状态，定位用模拟 GPS 并拦截上传，不发送真实位置。2026-11-18 已按用户配置的 Key 完成真实高德搜索、WGS84 转换和步行路线调用，以及网页路线预览；详见 VALIDATION.md。手机 HTTPS 和 GPS 外场仍未验证，真实范围与质量需针对演示区域验证。

## 官方依据

实现时已读取并核对：

- https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch
- https://lbs.amap.com/api/webservice/guide/api/newroute
- https://lbs.amap.com/api/webservice/guide/api/convert
- https://www.w3.org/TR/geolocation/

官方接口：[高德地点搜索](https://lbs.amap.com/api/uri-api/guide/search/marker) · [高德路线规划](https://lbs.amap.com/api/uri-api/guide/travel/route)。

## 校园网使用助手

手机访问 `https://电脑在校园网的地址:8443/assistant`。用与该地址匹配的证书。若手机已经安装并信任同一个项目 CA，可用该 CA 给新 IP 签证书，继续使用原来的手机 CA。浏览器把新的 IP、端口或协议组合视为另一个站点，首次访问需重新选该站点的麦克风、定位及助手设置。

1. 电脑查看 Wi-Fi IPv4 地址。以 `10.4.162.20` 为例，在项目目录执行：

   ```bash
   ip -brief -4 addr
   CAROOT="$PWD/certs/ca" mkcert -cert-file certs/campus.crt -key-file certs/campus.key 10.4.162.20
   ```

2. 在本地 `.env` 设置 `WEB_ACCESS_PASSWORD`。使用至少 16 位 ASCII 字符的随机口令，避免与其他服务共用。
3. 启动服务：

   ```bash
   .venv/bin/python scripts/serve.py --host 10.4.162.20 --port 8443 \
       --cert certs/campus.crt --key certs/campus.key --require-access
   ```

4. 手机连校园网并打开助手，登录用户名为 `3800`，密码为 `WEB_ACCESS_PASSWORD`。打开「权限与设置」，勾选需要的功能和「在这个浏览器记住以上设置」。进入新会话时点击「开启连续语音」，系统权限由浏览器管理。
5. 访问超时时，先核对手机能否到达电脑的 IP 和端口。校园网可能隔离客户端。真实眼镜还需要电脑能连接 ESP32 的视频和遥测端口，换网络后按板子串口报告修改 `ESP32_HOST`。网络隔离时，可继续用受控手机热点演示。

访问口令用于一台电脑上的单用户服务。使用同一个地址可保留浏览器的登录和授权偏好。自主手机 App 和独立高德 App 的结束回调仍需独立联调。
