# WorkBuddy V5 JaguarTV 多平台内容矩阵 MVP（本周版）

> **状态：已废弃。** 本版本包含团队权限与发布能力，不符合当前第一阶段范围。请改用 `WorkBuddy_V6_自动内容工厂_MVP_本周版.md`。

**版本：** v5.0-weekly-demo  
**目标：** 5 个工作日内完成可供团队共用的视频候选抓取、pt-BR 加工、审核和 Facebook / YouTube / TikTok / Kwai 发布闭环  
**原则：** 小步快跑、默认人工放行、授权优先、发布可追踪

## 1. 一句话需求

为 JaguarTV 团队提供一个共享内容工作台：每个成员管理自己的内容来源，系统收集和下载已获授权的视频，生成带 pt-BR 旁白/字幕与 JaguarTV CTA 的多平台版本，经审核后一键 API 发布或生成辅助发布包，并记录每条内容的发布地址和追踪链接。

## 2. 本周 Demo 的成功定义

本周结束前，必须现场演示以下完整链路：

```text
成员登录/选择身份
-> 使用自己的来源配置发现候选或提交 URL
-> 保存来源信息并检查重复
-> 上传/登记授权证据
-> 下载或导入素材
-> 生成 pt-BR 脚本、字幕、配音与品牌版本
-> 审核员批准
-> 发布到 FB / YT / TK / Kwai
-> 回写平台 post ID、公开 URL、发布时间与 UTM
```

Demo 验收数据：

- 至少 3 个团队成员配置，各自拥有独立来源和任务列表。
- 至少 10 条候选进入系统，其中至少 4 条完成全流程。
- 每条获批母内容生成 4 个平台发布版本或平台发布包。
- Facebook、YouTube、TikTok、Kwai 各完成至少 1 条真实账号发布。
- 有可用官方 API 权限的平台回写远端 post ID；没有权限的平台由发布员完成辅助上传并粘贴公开 URL。
- 每条发布内容均能追溯到成员、原始来源、权利证据、成片版本和追踪链接。

## 3. 本周范围

### 3.1 必须完成（P0）

1. 团队成员与角色配置。
2. 每人独立的来源、关键词和目标账号配置。
3. 候选 URL 手工提交。
4. Bilibili、YouTube 的关键词候选发现 Demo。
5. Bilibili、抖音、Facebook、YouTube、TikTok、Kwai 直接 URL 的元数据读取；下载能力按授权、登录状态和适配器可用性逐源启用。
6. URL、视频指纹和标题相似度去重。
7. Rights Gate：没有授权证据不能进入制作。
8. 下载/导入、ASR、pt-BR 脚本、字幕和 TTS。
9. JaguarTV Logo、结尾卡、CTA 和追踪链接。
10. 9:16 主版本和四个平台的元数据/发布包。
11. 审核页面：批准、退回、拒绝。
12. YouTube API 发布适配器。
13. Facebook、TikTok、Kwai 的 API 或辅助发布适配器。
14. 发布结果、失败原因、远端 ID 和 URL 回写。
15. 本地/微云归档与 `manifest.json`。

### 3.2 本周明确不做（Non-goals）

- 全网全自动巡检和无限关键词抓取。
- 自动处理验证码、绕过登录、访问限制或平台风控。
- 默认去水印、模糊作者标识、保留未知版权 BGM。
- 完全无人审核的公开发布。
- 复杂 CRM、佣金、广告投放、自动回复和用户生命周期。
- 高并发云架构、微服务拆分和多区域部署。
- 自动生成几十个矩阵账号或自动养号。
- 完整数据仓库；本周只记录发布和基础表现数据。

## 4. 两种发布模式

平台权限申请可能超过一周，因此发布模块必须同时支持两种模式。

### 4.1 API Publish

用于已具备 OAuth、应用审核和发布 scope 的账号：

- YouTube：YouTube Data API 上传，默认先使用 `private` 或 `unlisted` 验证。
- TikTok：Content Posting API，可配置 Direct Post 或草稿模式。
- Facebook：使用账号现有且获批的官方发布接口。
- Kwai：使用账号现有且获批的官方开放平台发布接口。

API 发布成功条件：返回远端 post ID，处理状态可查询，最终 URL 写回任务。

### 4.2 Assisted Publish

用于暂时没有 API 权限但已能正常登录的账号：

系统生成并锁定一个发布包：

```text
publish-package/<job_id>/<platform>/
├── video.mp4
├── cover.jpg
├── title.txt
├── description.txt
├── hashtags.txt
├── tracking-url.txt
├── rights-summary.txt
└── checklist.json
```

发布员从平台官方网页/App 上传，完成后粘贴公开 URL。系统校验 URL 域名并记录操作者和时间。辅助发布仍然属于正式发布闭环，不允许只把文件放到微云就标记成功。

## 5. 用户与权限

| 角色 | 本周能力 |
|---|---|
| Admin | 创建成员、绑定账号配置、管理品牌与全局规则 |
| Scout | 管理自己的来源、提交候选、登记授权证据 |
| Editor | 领取任务、编辑 pt-BR 脚本、生成平台版本 |
| Reviewer | 检查权利、语言、事实、品牌并批准/退回 |
| Publisher | 选择账号、API 发布或完成辅助发布、回写 URL |

Demo 可由一人兼任多个角色，但每个动作必须记录 `actor_id`。公开发布必须由 Reviewer 批准后才能执行。

## 6. 团队通用配置

### 6.1 `config/team.yaml`

```yaml
members:
  - id: allen
    name: Allen
    roles: [admin, scout, publisher]
    source_profiles: [allen_football_cn]
    target_accounts: [yt_hoje, tk_hoje]
  - id: editor_01
    name: Editor 01
    roles: [scout, editor]
    source_profiles: [editor_entertainment]
    target_accounts: [fb_hoje, kwai_hoje]
  - id: reviewer_br
    name: Reviewer BR
    roles: [reviewer]
    source_profiles: []
    target_accounts: []
```

### 6.2 `config/sources.yaml`

```yaml
profiles:
  - id: allen_football_cn
    owner_id: allen
    enabled: true
    platforms: [bilibili, douyin]
    content_pillars: [sports]
    languages: [zh-CN]
    keywords: [巴西足球, 内马尔, 巴甲, 世界杯]
    creator_allowlist: []
    daily_candidate_limit: 20
    rights_policy: authorized_only

  - id: editor_entertainment
    owner_id: editor_01
    enabled: true
    platforms: [youtube, facebook, tiktok, kwai]
    content_pillars: [entertainment_family]
    languages: [zh-CN, en, es]
    keywords: [family entertainment, movie guide]
    creator_allowlist: []
    daily_candidate_limit: 20
    rights_policy: authorized_only
```

### 6.3 `config/accounts.yaml`

```yaml
accounts:
  - id: yt_hoje
    platform: youtube
    brand: JaguarTV Hoje
    publish_mode: api
    credential_ref: secret://youtube/hoje
    default_visibility: private
    daily_limit: 2
    landing_page: https://copa.jarg.top/

  - id: tk_hoje
    platform: tiktok
    brand: JaguarTV Hoje
    publish_mode: assisted
    credential_ref: null
    daily_limit: 2
    landing_page: https://copa.jarg.top/
```

成员只能编辑自己拥有的来源配置，不能看到账号密钥。配置中的 `credential_ref` 只引用安全存储，不包含 token、cookie 或密码明文。

## 7. 内容范围与优先级

本周只做两个内容支柱：

| 支柱 | 占比 | 内容目标 | JaguarTV 连接 |
|---|---:|---|---|
| 足球热点 | 70% | 赛前背景、球员故事、技术解释、历史对比 | JaguarTV Hoje 对应赛事/节目内容页 |
| 安装与使用 | 30% | 手机、Android TV、TV Box、Downloader 教程 | 下载、注册、官方支持 |

每条候选必须标记：

- `searchable`：回答明确问题，例如“Como instalar na Android TV”。
- `shareable`：有值得转发的故事、观点或解释。
- `both`：同时满足搜索和分享。

本周不扩展娱乐、家庭、地区文化等支柱；数据模型保留字段，下周再决定是否增加。

## 8. 候选抓取规则

### 8.1 输入方式

1. **直接 URL：** 团队成员粘贴单条视频 URL，是本周最稳定的主路径。
2. **关键词发现：** 本周只要求 Bilibili 和 YouTube 返回候选列表，不自动下载。
3. **CSV 导入：** 支持批量导入 `owner_id, platform, url, rights_evidence`。

### 8.2 候选元数据

系统至少保存：

```json
{
  "source_platform": "youtube",
  "source_url": "https://...",
  "source_video_id": "...",
  "creator_name": "...",
  "title": "...",
  "published_at": "...",
  "duration_sec": 42,
  "view_count": 120000,
  "language": "es",
  "owner_id": "allen",
  "source_profile_id": "allen_football_cn",
  "discovered_at": "..."
}
```

### 8.3 去重

按以下顺序检查：

1. `platform + source_video_id` 精确重复。
2. URL 规范化后的重复。
3. 下载后计算视频感知哈希，识别跨平台相同片段。
4. 标题/字幕相似度超过阈值时提示 Reviewer，不自动拒绝。

### 8.4 Rights Gate

允许进入制作的依据：

- JaguarTV 自有素材。
- 作者/机构书面授权，覆盖商业改编和目标平台。
- 明确允许商业改编的许可证，并满足署名等条件。
- 平台内置 Remix/Stitch/Duet 等授权路径，且输出仍在其允许范围内。
- 其他情况经合规负责人逐条批准并记录依据。

没有权利证据只能保存元数据和联系作者，不能下载进入生产。画面、音乐、人物肖像和赛事转播权分别记录。

## 9. 视频生产流程

```text
INGESTED
-> RIGHTS_PENDING
-> READY
-> DOWNLOADED
-> ASR_DONE
-> SCRIPT_READY
-> RENDERED
-> REVIEW_PENDING
-> APPROVED
-> PUBLISHING
-> PUBLISHED
```

异常状态：`DUPLICATE`、`RIGHTS_REJECTED`、`PRODUCTION_FAILED`、`REVISION_REQUIRED`、`PUBLISH_FAILED`。

### 9.1 下载/导入

- 优先使用授权方提供的无水印母版。
- 下载适配器必须先检查平台、URL、授权状态和文件大小上限。
- 下载失败时允许成员上传本地授权文件。
- 不自动移除水印，不绕过 DRM，不处理验证码。

### 9.2 ASR 与脚本

- 自动识别源语言并生成带时间轴转写。
- 低置信度片段显示给 Editor 修改。
- 生成的 pt-BR 脚本不是逐句翻译，必须加入巴西观众可理解的背景和 JaguarTV 栏目角度。
- 比分、日期、人物、球队、产品代码和优惠必须人工确认。

### 9.3 音频

- Demo 默认提供一个 pt-BR TTS 声音，同时允许上传真人旁白。
- AI 配音不能表现为原人物亲口说葡语；发布元数据按平台规则披露。
- 原音乐没有明确商业跨平台许可时，替换为已授权音乐或无音乐版本。

### 9.4 渲染

本周只维护一个 9:16 母模板，输出四个平台副本：

- 1080x1920、H.264/AAC。
- 上方 JaguarTV 小角标，不遮挡主体。
- pt-BR 字幕位于移动端安全区。
- 结尾卡 2–3 秒，显示当前审核通过的 CTA。
- 每个平台单独生成标题、描述、hashtags、封面和 tracking URL。
- 不通过镜像、变速、裁切或去水印伪装原创。

## 10. JaguarTV CTA

本周只允许两种 CTA，避免版本混乱：

| CTA ID | 使用场景 | 目标 |
|---|---|---|
| `hoje_content_v1` | 足球热点 | JaguarTV Hoje 对应内容页或首页 |
| `install_android_v1` | 安装教程 | `https://jarg.top/mb` 或审核后的安装页 |

电视安装教程可展示 Downloader `2252960`。7 天试用、CazéTV、价格、佣金等主张只有在运营负责人确认后才能加入模板。

追踪链接：

```text
utm_source=<facebook|youtube|tiktok|kwai>
utm_medium=organic_social
utm_campaign=<hoje_sports|install_help>
utm_content=<job_id>_<platform>_<hook_version>
```

## 11. 审核界面

Demo 只需要一个任务队列页面：

- 左侧：我的候选、待制作、待审核、待发布、已发布、失败。
- 中间：原视频和四个平台预览。
- 右侧：来源、权利、pt-BR 脚本、CTA、目标账号和检查单。
- 操作：`批准`、`退回修改`、`拒绝`、`API 发布`、`生成发布包`、`登记发布 URL`。

发布前必须全部通过：

```text
[ ] 来源与作者已保存
[ ] 权利证据已批准
[ ] 音乐/肖像/赛事画面范围已检查
[ ] pt-BR 已审核
[ ] 事实与产品信息正确
[ ] AI 配音披露已设置
[ ] Logo、字幕、画面和音量正常
[ ] CTA 与 UTM 正确
[ ] 目标账号与发布时间正确
```

## 12. 最小数据模型

本周使用 SQLite 即可：

| 表 | 关键字段 |
|---|---|
| users | id, name, roles, active |
| source_profiles | id, owner_id, config_json, enabled |
| account_profiles | id, platform, publish_mode, credential_ref, config_json |
| candidates | id, owner_id, platform, url, source_video_id, metadata_json, status |
| rights | candidate_id, status, basis, evidence_path, reviewer_id, reviewed_at |
| jobs | id, candidate_id, editor_id, pillar, status, current_version |
| assets | id, job_id, kind, platform, path, checksum, version |
| reviews | job_id, reviewer_id, decision, checklist_json, note, created_at |
| publications | job_id, account_id, mode, status, remote_post_id, public_url, published_at |
| events | job_id, actor_id, event_type, payload_json, created_at |

文件按 `workspace/<owner_id>/<job_id>/` 隔离。SQLite 和所有操作日志每日备份。

## 13. 最小接口/命令

无论最终做 Web UI 还是 CLI，必须提供同等能力：

```bash
matrix source list --owner allen
matrix candidate add --owner allen --url <URL>
matrix candidate discover --profile allen_football_cn
matrix rights approve --candidate <ID> --evidence <FILE>
matrix job create --candidate <ID>
matrix job process --job <ID>
matrix job review --job <ID> --decision approve
matrix publish --job <ID> --account yt_hoje
matrix publish-package --job <ID> --account tk_hoje
matrix publication confirm --job <ID> --account tk_hoje --url <URL>
matrix job show --job <ID>
```

默认 `matrix publish` 只能发布为 private/draft。Reviewer 或 Publisher 在平台预览确认后再公开。

## 14. Demo 技术选型

本周以单机可运行为目标：

- Web/接口：FastAPI 或项目现有后端框架。
- 数据：SQLite。
- 队列：数据库任务表 + 单 worker，不引入消息队列。
- 视频：FFmpeg。
- ASR：本地 Whisper 或现有已验证 ASR 服务。
- 翻译/脚本：现有可用 LLM，输出结构化 JSON。
- TTS：一个已验证 pt-BR 引擎 + 真人音频上传兜底。
- 抓取：平台适配器接口；直接 URL 主路径，适配器不可用时允许导入授权文件。
- 发布：官方 API adapter + assisted adapter。
- 凭据：系统钥匙串、环境 secret 或团队现有 secret manager。

接口统一：

```python
class SourceAdapter:
    def inspect(url) -> SourceMetadata: ...
    def discover(profile) -> list[SourceMetadata]: ...
    def download(candidate, destination) -> DownloadResult: ...

class PublisherAdapter:
    def validate(account, asset) -> ValidationResult: ...
    def publish(account, asset, metadata) -> PublicationResult: ...
    def get_status(remote_post_id) -> PublicationStatus: ...
```

任何适配器不可用时返回明确的 `UNAVAILABLE`，不得伪造成功或调用未注册工具。

## 15. 五天执行表

| 时间 | 开发交付 | 运营交付 | 当日验收 |
|---|---|---|---|
| Day 1 | 项目骨架、SQLite、成员/来源/账号配置、候选提交 | 提供成员名单、账号、Logo、CTA、10 条授权样本 | 3 人可分别提交候选 |
| Day 2 | 元数据、去重、Rights Gate、下载/导入、文件归档 | 补齐授权证据与来源说明 | 无授权任务无法下载 |
| Day 3 | ASR、pt-BR 脚本、TTS、字幕、9:16 渲染 | pt-BR Reviewer 完成第一轮审校 | 2 条视频生成四平台版本 |
| Day 4 | 审核队列、YouTube API、四平台发布包、结果回写 | 完成四账号登录/OAuth，验证标题与 CTA | YouTube 私密上传成功；其他平台包可上传 |
| Day 5 | 失败重试、日志、微云归档、Demo 修复 | 四平台各发布至少 1 条并回写 URL | 完整链路现场演示 |

每天结束只保留一个可运行主分支；发现 API 权限阻断时当天切换 assisted publish，不等待平台审核。

## 16. Day 1 前置资料

以下资料必须最晚 Day 1 中午提供，否则不承诺真实账号发布，只能演示发布包：

1. Facebook、YouTube、TikTok、Kwai 的目标账号和账号责任人。
2. 已有 OAuth/API 应用、scope 和审核状态；没有则确认使用辅助发布。
3. 10 条可以用于 Demo 的授权视频或授权方无水印母版。
4. JaguarTV PNG Logo、结尾卡、品牌字体/颜色和 CTA 文案。
5. 两个最终落地页：足球内容、Android 安装。
6. pt-BR 审核人及每日可审核时间。
7. 微云目录和上传凭据的安全交付方式。

## 17. 本周验收标准

### 功能

- [ ] 3 个成员可使用各自来源配置。
- [ ] 候选可提交、发现、去重、领取和查询。
- [ ] 无权利批准的候选无法下载、加工和发布。
- [ ] 授权素材可完成 ASR、pt-BR 脚本、TTS/真人旁白、字幕和渲染。
- [ ] 一个母任务有四个平台资产和元数据。
- [ ] Reviewer 可批准、退回或拒绝。
- [ ] 至少一个 API adapter 完成真实私密/草稿上传。
- [ ] 四个平台均可生成可直接上传的发布包。
- [ ] 四个平台各有一条真实发布 URL 回写。
- [ ] 原始素材、授权、成片、发布记录和 manifest 可追溯。

### 质量与安全

- [ ] 不自动解决验证码、不绕过访问限制、不移除来源水印。
- [ ] 密钥、cookie、token 和密码不写入代码、SQLite、日志或 manifest。
- [ ] 同一发布动作重复执行不会产生重复帖子。
- [ ] 失败有错误码、可重试次数和人工接管入口。
- [ ] pt-BR、事实、品牌、权利和 CTA 均有审核记录。

## 18. Machine-Optimized Dispatch

以下文本可以直接替换原 WorkBuddy V4 的总任务：

```text
SYSTEM_ID: WORKBUDDY_V5_JAGUARTV_MATRIX_MVP
MODE: HUMAN_APPROVED_PIPELINE
DEADLINE: 5_WORKING_DAYS

OBJECTIVE:
Build a team-usable demo that ingests authorized non-pt-BR video candidates,
creates localized pt-BR JaguarTV variants, and publishes or prepares assisted
publication packages for Facebook, YouTube, TikTok, and Kwai.

SUCCESS:
- 3 isolated team source profiles.
- 10 ingested candidates.
- 4 fully processed authorized videos.
- 4 platform variants per approved video.
- At least 1 real publication URL per target platform.
- Source, rights, reviewer, owner, asset, CTA, UTM, account, remote ID, and URL
  are traceable for every publication.

P0_WORKFLOW:
INGEST -> DEDUPE -> RIGHTS_GATE -> DOWNLOAD_OR_IMPORT -> ASR -> PTBR_SCRIPT
-> AUDIO -> RENDER -> HUMAN_REVIEW -> API_OR_ASSISTED_PUBLISH -> RECORD_RESULT

HARD_GATES:
- Never download or process when rights.status != APPROVED.
- Never publish when review.status != APPROVED.
- Never remove creator watermarks to hide source.
- Never retain music without commercial cross-platform rights.
- Never solve CAPTCHA or bypass DRM/login/access controls.
- Never store secrets in source code, database records, logs, or manifests.
- Never report success without a remote post ID or a verified public URL.

CONTENT_SCOPE_THIS_WEEK:
- 70% football context, commentary, stories, and explainers.
- 30% JaguarTV installation and usage tutorials.
- Every item is SEARCHABLE, SHAREABLE, or BOTH.

TEAM_MODEL:
- Each candidate has owner_id and source_profile_id.
- Members may edit only their own source profiles.
- Account credentials are referenced, never exposed.
- Every state change records actor_id, timestamp, reason, and version.

PUBLISHING:
- Use official publishing APIs only when app/account authorization exists.
- Default API test uploads to private or draft.
- Otherwise generate a complete assisted publication package.
- Publisher records the verified public URL after manual upload.

FALLBACKS:
- Discovery unavailable -> direct URL or CSV input.
- Downloader unavailable -> import authorized master file.
- ASR low confidence -> editor transcription.
- TTS unavailable -> upload human narration.
- API unavailable -> assisted publication package.
- Cloud upload unavailable -> local archive marked PARTIAL_ARCHIVE.

DONE_STATE:
PUBLISHED_AND_TRACKED only when rights, pt-BR, facts, brand, and compliance are
approved; platform publication succeeded; and remote ID or verified URL plus UTM
are stored.
```

## 19. 官方发布能力依据

- [YouTube Data API: Upload a Video](https://developers.google.com/youtube/v3/guides/uploading_a_video) 支持 OAuth 2.0 授权的视频上传和元数据设置。
- [TikTok Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started/) 支持授权用户的 Direct Post 或相关发布流程，但需要注册应用和启用对应产品/权限。
- [快手开放平台 USER_VIDEO_PUBLISH](https://open.kuaishou.com/platform/openApi?menu=20) 提供视频发布能力，实际可用性取决于账号和 scope。

官方接口权限、审核和公开可见性可能成为外部依赖。例如未验证的 YouTube API 项目上传内容可能受私密可见性限制。因此本周 Demo 将“API 发布”和“辅助发布”同时作为正式能力，而不是承诺绕过平台审核。
