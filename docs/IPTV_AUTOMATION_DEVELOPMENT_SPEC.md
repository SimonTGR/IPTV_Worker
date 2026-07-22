# IPTV 自动化项目：剩余开发与云端部署任务

> 本文是后续 Codex 开发的唯一执行文档，旧版规划已经废弃。
>
> 项目目录：`D:\Simon\IPTV_1-master`
>
> 部署目标：不依赖家中电脑、不使用内网穿透，通过 GitHub Actions 执行抓取和检测，Cloudflare R2 保存输入/结果，Cloudflare Workers 提供动态源和最终订阅地址。

## 1. 当前结论

项目尚未全部开发完成，目前只完成了来源层的基础部分。

已经完成并应保留：

- `sources/base.py`：统一候选源数据模型。
- `sources/file_inbox.py`：本地 M3U/TXT 文件读取。
- `sources/normalize.py`：编码识别、M3U/TXT 解析和基础别名规范化。
- `sources/registry.py`：来源注册表、来源报告和状态文件。
- `utils/channel.py`：将来源注册表候选接入原有频道流程。
- `config/sources.json`：来源注册表雏形。
- `tests/`：8 个测试当前全部通过。
- `config/pending_sources/`：用户已经放入两个实际来源文件：
  - `iptv4.m3u`
  - `live-Simon-20260721-154729.m3u`

不要重写上述模块；应在现有实现上修复和扩展。

当前实际解析能力验证：如果直接扫描 `config/pending_sources/*.m3u`，可以读取2个文件、约1008个候选地址，其中约499个经过 `live.catvod.com` 中转；包含翡翠台、广东体育和 CCTV 等候选。

## 2. 当前阻塞问题

以下问题必须优先修复，否则不能开始部署。

### P0-1：实际文件目录没有被扫描

用户文件位于：

```text
config/pending_sources/
```

但 `config/sources.json` 当前扫描：

```text
config/local/inbox/
```

因此正式配置运行结果是0个文件、0个候选。

要求：

- 将来源注册表改为扫描：

```json
"paths": [
  "config/pending_sources/*.m3u",
  "config/pending_sources/*.m3u8",
  "config/pending_sources/*.txt"
]
```

- `config/pending_sources/` 是本地测试目录，也是云端工作流从 R2 下载输入文件后的落盘目录。
- 不要再同时维护 `config/local/inbox/` 和 `config/pending_sources/` 两套入口。
- `config/local/` 继续作为原项目普通本地源目录，不作为新的文件收件箱。

### P0-2：空目录错误地报告成功

`FileInboxAdapter.collect()` 在没有匹配文件时会得到：

```text
files=0
candidates=0
success=True
```

要求：

- 来源状态至少区分 `success`、`empty`、`failed`。
- `sources.json` 支持 `required: true/false`。
- `required=true` 且没有文件或候选时，本轮来源必须失败。
- 报告中写明 `no_files_matched` 或 `no_candidates_parsed`。
- 增加真实 `config/sources.json` 集成测试，不能只测试临时 fixture。

### P0-3：私密源文件可能进入 Git

当前 `.gitignore` 没有排除 M3U 输入、`.env` 和本地凭据。

至少增加：

```gitignore
config/pending_sources/*
!config/pending_sources/.gitkeep
.env
.env.*
output/*.m3u
output/*.txt
state/
workers/vendor/
```

要求：

- 输入 M3U 不提交 GitHub，即使使用私有仓库也不提交。
- Cloudflare、R2、GitHub Token 只放 GitHub Actions Secrets。
- 日志、报告和异常信息必须对 URL 中的 `token`、`tk`、`key`、`auth`、`sign` 等参数脱敏。

### P0-4：当前不是有效 Git 仓库

项目下的 `.git` 目录为空，`git status` 会失败。

要求：完成代码和本地测试后，再初始化为新的私有 GitHub 仓库。不要继续依赖原项目 fork 的工作流，也不要执行强制推送。

### P0-5：Docker Compose 使用上游镜像

当前 `docker-compose.yml` 使用 `guovern/iptv-api:latest`，其中不包含本项目新增功能。

本次目标采用 GitHub Actions + R2，不依赖 Docker Compose；如将来改用云服务器，必须构建本项目镜像或使用用户自己的镜像标签。

## 3. 最终云端架构

```text
Cloudflare R2（私有）
  input/pending_sources/*.m3u
              ↓
GitHub Actions：update-playlist.yml
  下载输入 → 抓取来源 → 规范化 → 测试 → 排序 → 验证
              ↓
Cloudflare R2（私有）
  output/user_result.m3u
  output/report.json
  state/source_state.json
  state/last_good_result.m3u
              ↓
Cloudflare Worker：iptv-list
  带访问令牌提供固定 M3U 地址
```

Worker 代码同步使用另一条链路：

```text
作者 GitHub：5d5d5f5f5f/abc
              ↓
GitHub Actions：sync-workers.yml
  检查 SHA → 下载允许文件 → 创建 Worker 新版本 → 冒烟测试
              ↓
测试通过才激活 iptv-gd / iptv-cz
失败则保留原活动版本
```

职责限制：

- GitHub Actions 负责 Python、FFmpeg、测速和码率检测。
- R2 只存储输入、输出和状态。
- `iptv-gd`、`iptv-cz` 只负责作者动态源逻辑。
- `iptv-list` 只负责安全提供最终 M3U。
- Cloudflare Worker 内不得尝试运行 FFmpeg 或完整 Python IPTV 流程。

## 4. 阶段一：修复来源入口和频道映射

### 4.1 修复收件箱

完成第2节全部 P0 项目，并增加以下测试：

- 正式 `config/sources.json` 能找到 `config/pending_sources/`。
- 空目录且 `required=true` 时失败。
- 空目录且 `required=false` 时状态为 `empty`，不伪装为成功。
- 输入文件不会被修改、移动或删除。
- 文件 SHA-256 未变化时复用解析缓存，避免每轮重复解析。
- 单文件损坏不能阻止其他文件继续解析。

### 4.2 补齐香港频道别名

最终输出仍严格由 `config/user_demo.txt` 控制，至少补齐：

```text
翡翠台 (Back up 1) -> 翡翠台
TVB Jade -> 翡翠台
珍珠台 -> 明珠台
Pearl -> 明珠台
J 2 -> TVB Plus
J2 -> TVB Plus
iNews 互動新聞台 -> 无线新闻台
TVB News -> 无线新闻台
VIUTV1 -> viuTV
ViuTV -> viuTV
Viu6 -> viuTV6
ViuTV6 -> viuTV6
鳳凰中文 -> 凤凰中文
鳳凰資訊 -> 凤凰资讯
```

要求：

- CCTV-5 与 CCTV-5+ 不能误匹配。
- 原始频道名必须保留在报告中。
- 别名只改变频道归类，不能证明流内容正确。
- 每个 `user_demo` 频道输出匹配数量统计。

### 4.3 验收

- 两个实际 M3U 均被读取。
- 只选择 `user_demo.txt` 中需要的频道。
- 分组和频道顺序与 `user_demo.txt` 完全一致。
- 报告列出未匹配频道和别名转换结果。

## 5. 阶段二：广东和潮州 Worker 来源适配

新增：

```text
sources/http_playlist.py
sources/worker_discovery.py
tests/test_worker_discovery.py
```

更新 `sources/registry.py`，支持：

```text
file_inbox
http_playlist
worker_discovery
```

接入现有地址：

```text
广东：https://iptv-gd.tanguangrun88.workers.dev
潮州：https://iptv-cz.tanguangrun88.workers.dev
```

Worker 适配器必须识别：

- HTTP 200 + M3U/TXT。
- HTTP 200 + 普通状态文本。
- HTTP 301/302/307/308 + `Location` 动态入口。
- Worker 本身作为播放代理 URL。
- 跳转循环、空响应、HTML 错误页和超时。

潮州 Worker 根路径实测会返回302动态地址，应视为发现成功，不得写死只有200才成功。

安全要求：

- 最多5次重定向。
- 只接受 HTTP/HTTPS 播放入口。
- 动态地址每轮刷新。
- 动态候选播放失败时只允许额外刷新一次。
- 不猜测频道路径；路径必须来自作者配置、实际播放清单或显式映射。
- 所有请求必须有超时和有限重试。

验收：

- 使用 mock HTTP 服务测试全部响应类型。
- 广东或潮州失效时，论坛 M3U 和其他来源仍能生成结果。
- 报告能说明 Worker 发现的最终入口和失败原因，但敏感参数必须脱敏。

## 6. 阶段三：测速、真实码率和伪直播过滤

当前项目只有下载速度、延迟、分辨率和简单广告过滤，不足以识别“能播放但内容是作者宣传视频”的来源。

### 6.1 指标分离

扩展 `ChannelData` 和 `TestResult`：

```text
playable
download_speed_mbps
bitrate_kbps
bitrate_estimated
delay_ms
resolution
fps
video_codec
audio_codec
success_ratio
consecutive_failures
content_verified
content_fingerprint
failure_reason
```

要求：

- 下载速度和视频码率不能共用 `speed` 字段。
- 码率优先读取 FFprobe JSON 的 `format.bit_rate` 或视频流 `bit_rate`。
- 无直接码率时可以按媒体采样估算，但必须标记为估算。
- FFprobe 失败不能导致整个任务崩溃。

### 6.2 内容正确性检测

`live-Simon-20260721-154729.m3u` 中大量地址经过 `live.catvod.com`。这些地址可能返回持续滚动的 HLS 清单，即使实际画面是宣传或提示视频，现有 `#EXT-X-ENDLIST` 检查也无法识别。

要求：

- 不直接封禁整个域名。
- 将带统一令牌的中转域名标为 `untrusted_relay`。
- 对准备进入每频道前3名的候选抽取少量帧和音频，生成感知指纹。
- 三个或更多无关频道出现相同/高度相似指纹时，标记 `placeholder_fingerprint`。
- 检测60～120秒范围内的短循环宣传视频。
- 支持 `known_bad_fingerprints` 持久化。
- 无法验证内容的中转源标记 `content_unverified`，在存在已验证来源时不能成为主源。
- 发现宣传视频后只淘汰该 URL，不能尝试把宣传视频“转换”为真实直播。

失败原因至少包括：

```text
timeout
http_error
invalid_playlist
no_media_stream
speed_too_low
bitrate_too_low
resolution_too_low
wrong_content
placeholder_fingerprint
relay_token_expired
content_unverified
ad_or_no_signal
```

### 6.3 排序

默认排序：

```text
playable
content_verified
stability
download_speed
bitrate
resolution
delay
source_priority
```

要求：

- 每个频道默认保留3条。
- 第一条为主源，后两条为备用源。
- 在质量接近时优先选择不同 Host 的备用源。
- 一次网络抖动不能立刻删除历史稳定源。
- 连续失败达到阈值后淘汰，后续轮次允许恢复。
- 排序必须有稳定 tie-breaker，保证相同输入生成相同结果。

## 7. 阶段四：安全输出、报告与回退

输出：

```text
output/user_result.m3u
output/report.json
state/source_state.json
state/last_good_result.m3u
```

正式发布条件：

- 没有未处理异常。
- M3U 可以被重新解析。
- 输出不是空文件。
- 频道覆盖率达到配置阈值，默认70%。
- CCTV、广东、卫视等关键组至少存在一个有效频道。
- 新结果没有出现异常大幅下降；下降超过阈值时暂停发布并保留旧版。

发布步骤：

1. 写入同目录临时文件。
2. 重新解析临时 M3U。
3. 生成本轮报告。
4. 备份当前正式文件为 last-known-good。
5. 使用原子替换发布。
6. 任一步骤失败则保持正式结果不变。

报告至少包含：

- 来源成功、为空或失败状态。
- 输入文件 SHA、编码和候选数量。
- 每个频道候选数量、有效数量和选中来源。
- 下载速度、码率、分辨率、延迟和稳定性。
- 淘汰 URL 的脱敏值及原因。
- 与上一轮相比新增、删除和主源变化。
- 是否正式发布；未发布时说明回退原因。
- 当前 Worker 上游 SHA 和 Cloudflare 活动版本。

## 8. 阶段五：作者 GitHub JS 自动同步到 Worker

新增：

```text
workers/__init__.py
workers/manifest.json
workers/github_upstream.py
workers/cloudflare.py
workers/validate.py
workers/sync.py
tests/test_worker_sync.py
```

`workers/manifest.json` 至少配置：

- 上游仓库：`5d5d5f5f5f/abc`。
- 固定分支或 ref。
- 潮州文件：`潮州/chaozhou.js`。
- 广东文件的真实路径：实现前必须从仓库确认，禁止猜测。
- Cloudflare Worker 名称：`iptv-cz`、`iptv-gd`。
- 文件大小上限。
- 允许状态码和冒烟测试规则。

同步流程：

1. 使用 GitHub API 获取上游 blob SHA/commit SHA。
2. SHA 未变化时退出，不重复部署。
3. 只下载 manifest 允许的仓库和文件。
4. 校验非空、不是 HTML/404 页面、体积符合限制。
5. 计算 SHA-256 并保存审计记录。
6. 通过 Cloudflare API 创建新 Worker 版本。
7. 测试预览或新版本：
   - 广东按真实行为验证。
   - 潮州允许200或带有效 Location 的30x。
8. 测试通过后才切换活动版本。
9. 失败时保留旧活动版本。
10. 发布成功后触发一次直播列表更新。

必须提供：

```text
python -m workers.sync --check
python -m workers.sync --deploy chaozhou
python -m workers.sync --deploy guangdong
python -m workers.sync --deploy-all
```

模式：

```text
check-only
manual
auto
```

初次上线使用 `manual`，连续验证成功后才能启用 `auto`。

禁止事项：

- 不允许 Worker 代码读取用于部署的 Cloudflare Token。
- 不把第三方仓库任意新文件自动纳入部署。
- 不在日志中输出 Token、账户 ID 或完整请求头。
- 测试失败时不得覆盖现有 Worker。

## 9. 阶段六：Cloudflare R2 和最终订阅 Worker

### 9.1 R2 Bucket

创建一个私有 Bucket，例如：

```text
iptv-simon
```

对象结构：

```text
input/pending_sources/iptv4.m3u
input/pending_sources/live-Simon-20260721-154729.m3u
output/user_result.m3u
output/report.json
state/source_state.json
state/last_good_result.m3u
```

要求：

- 输入和输出 Bucket 默认不公开。
- GitHub Actions 使用最小权限的 R2 S3 API 凭据。
- 工作流开始时下载 `input/pending_sources/` 到本地 `config/pending_sources/`。
- 成功发布后上传 output 和 state。
- 失败时不得覆盖 R2 中的 last-known-good。

### 9.2 最终订阅 Worker

创建第三个 Worker：

```text
iptv-list
```

绑定私有 R2 Bucket，并提供：

```text
GET /m3u?token=...
GET /health
GET /report?token=...
```

要求：

- `/m3u` 从 R2 返回 `output/user_result.m3u`。
- Content-Type 使用 M3U 可识别类型，并采用 UTF-8。
- 使用 Worker Secret 保存订阅访问令牌。
- Token 错误返回401/403。
- `/health` 不泄露源 URL，只返回更新时间、文件是否存在和状态。
- `/report` 必须鉴权，且报告中的敏感 URL 已脱敏。
- 支持 ETag/Last-Modified，避免播放器重复下载。
- 不代理所有视频流；只提供播放列表文件。

最终播放器订阅格式：

```text
https://iptv-list.<subdomain>.workers.dev/m3u?token=<PLAYLIST_TOKEN>
```

## 10. 阶段七：GitHub Actions

不要直接使用现有 `.github/workflows/main.yml`。它只有手动触发、包含原项目分支逻辑和强制推送，不适合本项目。

### 10.1 更新直播列表

新增：

```text
.github/workflows/update-playlist.yml
```

必须支持：

- `workflow_dispatch` 手动执行。
- `schedule` 定时执行，初始每天一次，避开整点。
- `concurrency`，同一时间只允许一个更新任务。
- 合理 `timeout-minutes`，超时后保留 R2 旧结果。
- Python 3.13、Pipenv 和 FFmpeg。
- 安装锁定依赖。
- 从 R2 下载输入和历史 state。
- 运行测试或最小预检。
- 执行一次更新，不启动长期 Flask/GUI 服务。
- 验证正式结果。
- 成功后上传 R2。
- 保存脱敏日志为短期 Actions Artifact。

GitHub Actions 运行节点可能不在中国或香港。若出现“用户网络可播放但 Actions 判断失败”的区域源问题，报告必须区分 `runner_region_unreachable`，不能永久拉黑该源。后续可迁移到香港/华南云服务器，但第一版先使用 Actions。

### 10.2 同步 Worker

新增：

```text
.github/workflows/sync-workers.yml
```

必须支持：

- 手工执行 `check` 和 `deploy`。
- 定时检查上游 SHA，建议每6小时一次。
- SHA 未变化时快速退出。
- 调用 `workers.sync`，不能在 YAML 中复制业务逻辑。
- 部署失败时工作流失败，但原 Worker 保持活动。
- 保存不含秘密的同步报告。

### 10.3 GitHub Secrets

使用以下 Secrets，名称可在实现时统一：

```text
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_ENDPOINT
R2_BUCKET
PLAYLIST_TOKEN
UPSTREAM_GITHUB_TOKEN
```

所有 Secrets 必须在启动时验证是否存在，但不得打印值。

## 11. 测试要求

继续保留当前8个测试，并新增：

### 来源与别名

- 真实配置路径能够读取 `pending_sources`。
- required 空来源失败。
- 文件 SHA 缓存有效。
- 香港别名全部映射到 `user_demo` 名称。
- CCTV-5/CCTV-5+ 不误匹配。

### Worker

- 200 M3U、200文本、30x动态入口。
- 重定向循环和非法协议。
- 动态入口刷新一次后恢复。
- 上游 SHA 不变不部署。
- 新 Worker 测试失败不切流。

### 测速与内容

- 下载速度和码率字段分离。
- FFprobe 码率解析。
- 不同 URL 返回相同宣传视频时被识别。
- 相同频道主备源相似时不误判为跨频道占位。
- Token 过期返回宣传视频时不能成为主源。
- 稳定源优先于偶发高速源。

### 发布与云端

- M3U 原子替换。
- 频道覆盖率不足时回退。
- R2 上传失败时保留旧对象。
- `iptv-list` 鉴权、ETag 和 Content-Type。
- 日志和报告敏感参数脱敏。

所有外部服务测试使用 mock；CI 测试不能依赖真实直播源、真实 Cloudflare账户或第三方仓库实时状态。

## 12. Codex 开发顺序

Codex 必须按照以下顺序执行，每阶段完成后停止并报告测试结果，不得一次性大改：

1. 修复 P0 问题和真实来源目录。
2. 补齐香港别名与真实配置集成测试。
3. 实现 Worker 来源适配器。
4. 实现码率、稳定性和内容指纹过滤。
5. 实现原子发布、报告和 last-known-good。
6. 实现作者 JS 同步和 Worker 安全部署。
7. 实现 R2 输入/输出客户端。
8. 实现 `iptv-list` Worker。
9. 实现两个 GitHub Actions 工作流。
10. 完成本地测试和云端首次手工验收。
11. 手工运行稳定后再开启定时任务和 Worker `auto` 模式。

每阶段报告必须包含：

- 修改文件列表。
- 运行的测试及结果。
- 尚未实现的内容。
- 新增配置项。
- 是否涉及外部账户操作。
- 是否可以安全进入下一阶段。

## 13. 首次部署步骤

全部代码完成并测试通过后，按以下顺序部署：

1. 初始化新的 Git 仓库。
2. 检查 `.gitignore`，确认两个 M3U、输出文件和所有凭据不会提交。
3. 创建用户自己的私有 GitHub 仓库并推送代码。
4. 在 Cloudflare 创建私有 R2 Bucket。
5. 将两个 M3U 上传到 `input/pending_sources/`。
6. 创建最小权限 R2 API 凭据。
7. 创建最小权限 Cloudflare Workers API Token。
8. 在 GitHub 仓库配置 Actions Secrets。
9. 部署 `iptv-list` Worker 并绑定 R2。
10. 手工运行 `sync-workers.yml` 的 `check` 模式。
11. 手工运行 `update-playlist.yml`。
12. 检查 Actions 日志、`output/report.json` 和 R2 结果。
13. 用播放器测试 `iptv-list` 的订阅地址。
14. 手工部署一个 Worker 新版本并验证回滚路径。
15. 连续成功至少3次后，开启每天一次列表更新。
16. Worker 同步继续使用 `manual`；确认稳定后再改为 `auto`。

## 14. 最终验收标准

只有以下条件全部满足才算开发和部署完成：

- R2 中两个输入 M3U 都能被读取。
- 最终频道和分组严格来自 `user_demo.txt`。
- 广东、潮州 Worker 每轮能重新发现动态入口。
- 论坛源、Worker 源和订阅源在同一候选池竞争。
- 下载速度、真实码率、分辨率、延迟和稳定性分开记录。
- 宣传视频和内容错误源不会因为速度快而成为主源。
- 每频道最多3个来源，并尽量来自不同 Host。
- 任一来源失败不会清空全部结果。
- 失败更新不会覆盖 R2 上一份可用列表。
- 作者 GitHub JS 变化时，只有测试通过的新 Worker 版本才会激活。
- 用户无需运行家中电脑，也不需要内网穿透。
- 播放器能通过带访问令牌的固定 HTTPS 地址获取最终 M3U。
- GitHub、Cloudflare 和 R2 凭据没有进入代码、提交历史、日志或报告。

## 15. 本地开发完成记录（2026-07-22）

以下功能现已实现并通过本地 mock 测试，尚未连接任何真实云端账户：

- `cloud/r2.py`：R2 S3 兼容客户端，支持受限对象读取、列表和上传；使用环境变量凭据和 SigV4 签名，不输出密钥。
- `cloud/workflow.py`：下载 R2 输入与历史状态、运行一次更新、仅在 `report.json` 标记为 `published: true` 后上传新列表与状态。失败时不会覆盖 R2 的可用结果。
- `workers/iptv_list.js`：最终订阅 Worker，提供 `/m3u?token=...`、`/report?token=...` 与不泄露源地址的 `/health`，支持 ETag 和 Last-Modified。
- `.github/workflows/update-playlist.yml`：每天一次的列表更新，带并发锁、超时、FFmpeg、测试和短期脱敏报告。
- `.github/workflows/sync-workers.yml`：仅手动触发的广东/潮州上游 Worker 同步；仍保持 `manual` 模式。
- `.gitignore`：额外排除了运行日志、缓存、R2 输入文件与 Wrangler 临时文件。

本轮新增的本地测试覆盖 R2 签名与对象安全、输入下载、未发布结果不上传、最终 Worker 路由/鉴权约束，以及 Actions 的关键安全配置。实际部署仍必须按第 13 节由用户创建私有仓库、R2 Bucket、Cloudflare Worker 绑定和 GitHub Secrets 后手工验收。
