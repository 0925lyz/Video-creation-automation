# WorkBuddy V6 自动内容工厂 MVP（本周版）

**阶段：** Phase 1 - 批量抓取、自动二创、pt-BR 本地化、发布就绪  
**工期目标：** 本周形成可运行 Demo  
**暂不包含：** 用户权限、Admin、账号管理、自动发布

## 1. 目标

构建一个单机可运行、团队共享配置的自动视频生产工具：从 Bilibili、抖音、YouTube、TikTok、Facebook、Kwai 批量发现非葡语视频，自动筛选、下载、去重、选择高光、重构剪辑，生成 pt-BR 脚本、语音和字幕，叠加 JaguarTV 品牌素材，输出可直接人工审核和发布的视频包。

```text
READY_FOR_REVIEW =
  source_collected
  AND language_not_portuguese
  AND source_downloaded
  AND duplicate_check_passed
  AND highlight_selected
  AND ptbr_script_generated
  AND ptbr_audio_generated
  AND ptbr_subtitles_generated
  AND video_rendered
  AND automated_quality_check_passed
  AND publication_package_created
```

后台人工只审核 `READY_FOR_REVIEW` 成片并决定通过、退回或拒绝。抓取和加工过程不设置成员、角色或授权审批前置流程。

## 2. 本周 Demo 验收目标

- 一份 YAML 配置定义平台、关键词、种子账号、内容分类和抓取数量。
- 一条命令启动批量生产，不逐条提交 URL。
- 自动发现至少 100 条非 `pt/pt-BR/pt-PT` 候选。
- 自动下载至少 20 条可用源视频。
- 自动生成至少 5 条 20–60 秒 pt-BR 竖屏成片。
- 每条成片具有新的 pt-BR 开头、旁白、字幕、剪辑结构和 JaguarTV CTA。
- 每条成片附四个平台的标题、描述、hashtags、封面和 manifest。
- 失败任务有错误码，可重试且不会重复下载或重复生产。
- 运营人员可直接打开 `ready_for_review/` 查看和审核。

本周不以发布数量验收，自动发布整体移到 Phase 2。

## 3. 范围

### P0

1. 多平台关键词、种子账号和趋势候选发现。
2. 非葡语识别与排除。
3. 热度、时效、内容价值、巴西相关度和可剪辑性评分。
4. URL、文本、音频和画面去重。
5. 批量下载、限速、失败隔离和重试。
6. ASR、说话片段、场景和主体分析。
7. 自动高光选择与 20–60 秒叙事重构。
8. pt-BR 改写、TTS 和字幕时间轴。
9. 9:16 智能构图、原字幕处理、Logo 与结尾卡。
10. 自动质量检测、发布包和人工审核目录。

### 不做

- Admin、登录、成员权限、任务分配。
- Facebook、YouTube、TikTok、Kwai 自动发布。
- CRM、UTM 回传、数据看板和广告投放。
- 分布式微服务、云端高可用和大规模账号管理。
- 自动做出最终审核结论。

## 4. 流水线

```mermaid
flowchart LR
  A["关键词 / 种子账号 / 趋势"] --> B["多平台候选抓取"]
  B --> C["语言过滤"]
  C --> D["评分与去重"]
  D --> E["批量下载"]
  E --> F["ASR / 场景 / 主体分析"]
  F --> G["高光选择与脚本重构"]
  G --> H["pt-BR 改写 / TTS / 字幕"]
  H --> I["9:16 剪辑与品牌渲染"]
  I --> J["自动质量检查"]
  J -->|通过| K["ready_for_review"]
  J -->|失败| L["failed / retry"]
```

状态：

```text
DISCOVERED -> FILTERED -> QUEUED -> DOWNLOADED -> ANALYZED
-> SCRIPTED -> VOICED -> RENDERED -> QA_PASSED -> READY_FOR_REVIEW
```

异常状态：`LANGUAGE_REJECTED`、`DUPLICATE`、`DOWNLOAD_FAILED`、`ASR_FAILED`、`NO_HIGHLIGHT`、`VOICE_FAILED`、`RENDER_FAILED`、`QA_FAILED`。

## 5. 抓取系统

### 来源模式

| 模式 | 说明 | 优先级 |
|---|---|---:|
| 关键词搜索 | 按平台和多语言关键词定时搜索 | P0 |
| 种子账号 | 扫描指定优质账号近期视频 | P0 |
| 热榜/趋势 | 平台热榜、Google Trends、人工趋势词 | P1 |
| URL/CSV | 补充和适配器失败兜底 | P0 |

直接 URL 不是主工作流，但必须保留以便快速补充高质量素材。

### Adapter 接口

```python
class SourceAdapter:
    platform: str

    def search(self, keyword: str, limit: int) -> list[Candidate]: ...
    def crawl_creator(self, creator_url: str, limit: int) -> list[Candidate]: ...
    def inspect(self, url: str) -> Candidate: ...
    def download(self, candidate: Candidate, output_dir: str) -> DownloadResult: ...
```

### 首周适配策略

| 平台 | 候选发现 | 下载 |
|---|---|---|
| YouTube | 搜索词 + 种子频道 | `yt-dlp` |
| Bilibili | 搜索词 + UP 主 | `yt-dlp`/独立 adapter |
| 抖音 | 搜索页 + 种子账号 adapter | `yt-dlp`/独立 adapter/文件导入兜底 |
| TikTok | 搜索页 + 种子账号 adapter | `yt-dlp`/独立 adapter/文件导入兜底 |
| Kwai | 搜索页 + 种子账号 adapter | 独立 adapter/文件导入兜底 |
| Facebook | 种子 Page/Reels | `yt-dlp`/独立 adapter/文件导入兜底 |

适配器必须故障隔离。需要登录的平台读取运营提供的浏览器会话引用，配置中不保存 cookie 内容。出现验证时将该 adapter 标记 `DEGRADED`，其他平台继续运行。

### 默认规模

```yaml
discovery:
  candidates_per_run: 500
  max_candidates_per_keyword: 20
  max_candidates_per_creator: 10
  metadata_workers: 6
download:
  max_items_per_run: 50
  workers: 3
  max_duration_sec: 600
  min_height: 720
  max_file_size_mb: 500
```

单个平台错误率连续超过 30% 时暂停 30 分钟，不能无限重试。

## 6. 语言过滤

系统定义：`detected_language NOT IN [pt, pt-BR, pt-PT]` 才进入主队列。

识别顺序：

1. 平台元数据语言。
2. 标题与描述语言。
3. 前 30–60 秒音频采样的 ASR 语言识别。
4. 信息冲突时以音频为主；置信度低于 0.70 标记 `LANGUAGE_UNCERTAIN`。

纯音乐、无对白和环境声视频标记 `NO_SPEECH`，允许进入视觉内容队列并生成全新的 pt-BR 解说。

## 7. 候选评分

| 指标 | 分数 | 说明 |
|---|---:|---|
| 近期传播速度 | 25 | 播放/发布时间，按账号体量归一 |
| 巴西受众兴趣 | 20 | 足球、娱乐、家庭、奇闻、技能等配置匹配 |
| 完整故事潜力 | 20 | 有冲突、结果、解释或明确高光 |
| 可剪辑性 | 15 | 画质、主体、镜头和音频质量 |
| 新颖性 | 10 | 与历史素材、画面和脚本不重复 |
| JaguarTV 连接度 | 10 | 可自然连接观看、娱乐或安装场景 |

- `>=75`：自动下载。
- `60–74`：候补。
- `<60`：只保留元数据。

固定播放量/点赞数是可选过滤条件，不再作为唯一选片标准。

## 8. 去重

四层去重：

1. 平台视频 ID 和规范化 URL。
2. 标题、描述和 ASR 文本相似度。
3. 音频指纹。
4. 关键帧视频感知哈希。

相似度 `>=0.90` 自动跳过；`0.75–0.90` 允许生产，但必须选择不同片段或叙事角度。

## 9. 自动高光与二创

每条源视频生成：

- 完整 ASR 和时间轴。
- 场景切分点。
- 人脸/主体位置轨迹。
- 音量峰值、欢呼、笑声等音频事件。
- OCR 文本和原字幕区域。
- 关键帧描述。
- 15–90 秒候选片段。

高光评分：

```text
semantic_completeness  30%
visual_action          20%
emotion_audio_peak     15%
hook_strength          15%
source_engagement_hint 10%
editability            10%
```

默认叙事模板：

```text
0–2 秒      pt-BR 原创钩子
2–8 秒      背景/问题
8–45 秒     重排后的核心高光与解说
45–55 秒    结论/反转/观点
最后 2–3 秒 JaguarTV CTA
```

系统需要删除无关开场、等待、重复和空白，只保留支持新叙事的片段，并允许重新排序。每条母素材生成 1–3 个候选方案；没有完整故事或看点时标记 `NO_HIGHLIGHT`，不强行生产。

## 10. pt-BR 本地化

```json
{
  "hook": "...",
  "narration_segments": [
    {"start": 0.0, "end": 4.2, "text": "...", "tone": "energetic"}
  ],
  "closing": "...",
  "title": "...",
  "description": "...",
  "hashtags": ["..."],
  "content_category": "football",
  "content_type": "shareable"
}
```

要求：

- 使用自然巴西葡语，不使用葡萄牙葡语书面表达。
- 人名、球队、赛事、比分和术语保持准确。
- 俚语按巴西文化改写，不逐字翻译。
- 每句优先 4–12 个词，适配短视频语速。
- 每条必须有新的钩子、背景、观点或结论。
- 标题准确表达看点，不生成与画面无关的承诺。

TTS 至少提供 `energetic_male` 和 `warm_female` 两种 pt-BR 声音。TTS 失败进入 `VOICE_FAILED`，不得使用其他语言替代。

## 11. 字幕与画面

原字幕处理顺序：

1. OCR 检测字幕区域和出现频率。
2. 优先使用 inpainting 清理固定字幕区。
3. 修复质量不通过时局部模糊或重新构图。
4. 无法可靠处理时保留并标记 `SOURCE_TEXT_VISIBLE`，交人工审核。

pt-BR 字幕：

- 单行优先、最多两行，每行不超过 32–36 字符。
- 根据 TTS 时间轴生成短句或逐词字幕。
- 位于移动端安全区，不遮挡人物、球、比分和动作。
- 使用高对比字色与描边，不使用大面积黑块。

9:16 输出：

- 1080x1920、H.264、AAC。
- 主体跟踪决定裁切中心。
- 横屏允许主体裁切或背景扩展。
- JaguarTV 角标处于安全区，结尾卡 2–3 秒。

## 12. JaguarTV 品牌

第一阶段只使用：透明 Logo、统一字幕样式、结尾 CTA 卡。

| 内容类型 | CTA |
|---|---|
| 体育/娱乐/家庭 | 引导 JaguarTV Hoje 发现值得看的内容 |
| 安装/教程 | Android 下载或 TV Box 安装 |
| 其他试验内容 | 只显示品牌，不强推链接 |

品牌资产位于 `assets/brand/`，路径从配置读取。

## 13. 自动质量检查

| 检查 | 门槛 |
|---|---|
| 可播放 | FFprobe 成功，音视频轨存在 |
| 时长 | 20–60 秒 |
| 分辨率 | 1080x1920 |
| 黑帧 | 连续不超过 0.5 秒 |
| 冻结帧 | 非设计冻结不超过 1 秒 |
| 音频 | 无削波、长静音，TTS 清晰 |
| 字幕 | 不越界、无空字幕、字符不溢出 |
| 布局 | Logo、字幕、人脸和主体不冲突 |
| 语言 | 成片 ASR 复检为 pt/pt-BR，置信度 >=0.85 |
| 重复 | 与历史成片不过度相似 |

QA 失败最多自动重渲染 2 次，之后进入 `needs_manual_fix/`。

## 14. 输出结构

```text
workspace/
├── candidates/
├── downloads/
├── jobs/<job_id>/
│   ├── source.json
│   ├── source.mp4
│   ├── transcript_source.json
│   ├── edit_plan.json
│   ├── script_ptbr.json
│   ├── voice_ptbr.wav
│   ├── subtitles_ptbr.srt
│   ├── master_9x16.mp4
│   ├── cover.jpg
│   ├── metadata.json
│   ├── manifest.json
│   └── logs/
├── ready_for_review/<job_id>/
│   ├── video.mp4
│   ├── cover.jpg
│   ├── metadata.json
│   ├── source-preview.mp4
│   └── review.json
├── approved/
├── rejected/
├── needs_manual_fix/
└── failed/
```

`review.json`：

```json
{"decision":"pending|approved|revision_required|rejected","note":"","reviewed_at":""}
```

## 15. 四平台发布包

第一阶段不调用发布接口，但必须生成：

```json
{
  "job_id": "uuid",
  "youtube": {"title":"...","description":"...","hashtags":[]},
  "tiktok": {"caption":"...","hashtags":[]},
  "kwai": {"caption":"...","hashtags":[]},
  "facebook": {"text":"...","hashtags":[]},
  "cta_url": "https://copa.jarg.top/",
  "review_status": "pending"
}
```

四个平台本周可共用同一成片。Phase 2 再增加平台独立版本和自动发布 adapter。

## 16. 配置

主配置已提供：[pipeline.example.yaml](/Users/allen/Documents/视频二创/config/pipeline.example.yaml)。关键词与种子账号分别使用：[keywords.example.yaml](/Users/allen/Documents/视频二创/config/keywords.example.yaml)、[creators.example.yaml](/Users/allen/Documents/视频二创/config/creators.example.yaml)。

数量、并发、时长、来源、语言、阈值、TTS、字幕、品牌和输出策略均必须配置化。

## 17. 命令行

```bash
workbuddy doctor
workbuddy discover --config config/pipeline.yaml
workbuddy download --limit 50
workbuddy produce --limit 10
workbuddy run --discover 500 --download 50 --produce 10
workbuddy status
workbuddy retry --status QA_FAILED --limit 5
workbuddy review
```

所有生产命令支持 `--dry-run`，只显示平台、关键词、数量、估算磁盘和任务数。

## 18. 技术实现

本周采用单机模块化单体：

| 能力 | 实现建议 |
|---|---|
| 任务与元数据 | SQLite |
| 平台下载 | 已安装的 `yt-dlp` + 独立 adapter |
| 浏览器候选发现 | Playwright 持久化浏览器会话 |
| 视频处理 | FFmpeg/FFprobe |
| 场景检测 | PySceneDetect |
| ASR/语言识别 | faster-whisper/Whisper |
| 音频活动 | Silero VAD 或等价实现 |
| 主体跟踪 | OpenCV + MediaPipe/视觉模型 |
| 感知去重 | pHash + 音频指纹 |
| pt-BR 脚本 | 可用 LLM，结构化 JSON 输出 |
| TTS | 已验证 pt-BR TTS 服务或本地引擎 |
| 审核 | 本地静态页面或轻量 Web 页面 |

当前环境已有 `yt-dlp 2026.07.04`，但没有 FFmpeg、Whisper 和 PySceneDetect。`workbuddy doctor` 必须先检查并安装这些依赖，再用一条本地样片验证。

## 19. 可靠性

- 每一步输入输出写入 manifest。
- `platform + source_id` 是抓取幂等键。
- `source_checksum + edit_template_version` 是生产幂等键。
- 单步骤最多重试 2 次，使用指数退避。
- 失败不删除中间产物，可从最近成功步骤继续。
- 每次运行设置下载、生产、磁盘和时长上限。
- 剩余磁盘低于 20 GB 时停止新下载。
- 日志不记录 cookie、token 或密码。

## 20. 五天实现顺序

| 日期 | 核心开发 | 当日结果 |
|---|---|---|
| Day 1 | doctor、FFmpeg/ASR、配置、SQLite、YouTube/Bilibili 抓取下载 | 100 候选、5 个下载 |
| Day 2 | 其他 adapters、语言过滤、评分、去重、队列 | 六平台统一队列，故障隔离 |
| Day 3 | ASR、场景、高光、pt-BR 脚本、TTS | 3 条生成音频和剪辑计划 |
| Day 4 | 9:16、原字幕、字幕、Logo、结尾卡 | 3 条成片进入 QA |
| Day 5 | QA、发布包、审核目录、批量重试 | 100 候选、20 下载、5 成片 |

某个平台不稳定时标记 `DEGRADED`，不能阻塞其他来源；该平台用批量 URL/CSV 或文件导入兜底。

## 21. 验收清单

### 抓取

- [ ] 配置一次即可批量运行。
- [ ] 六个平台具有统一 adapter 和独立状态。
- [ ] 一次至少发现 100 条非葡语候选。
- [ ] 至少下载 20 条，失败有明确原因。
- [ ] 相同视频不会重复下载和生产。

### 二创

- [ ] 自动生成 ASR、场景、关键帧和高光。
- [ ] 自动选择 20–60 秒完整片段。
- [ ] pt-BR 脚本包含新钩子、背景和结论，不是整段直译。
- [ ] TTS 与 pt-BR 字幕基本同步。
- [ ] 输出清晰的 1080x1920 竖屏视频。
- [ ] 原字幕、主体裁切、Logo、CTA 不遮挡关键画面。

### 交付

- [ ] 至少 5 条进入 `ready_for_review/`。
- [ ] 每条包含视频、封面、四平台文案、源预览和 manifest。
- [ ] QA 失败可重试或进入人工修复。
- [ ] 人工可批准、退回或拒绝。
- [ ] 全流程由一条 `workbuddy run` 启动。

## 22. Machine-Optimized Dispatch

```text
SYSTEM_ID: WORKBUDDY_V6_AUTO_CONTENT_FACTORY
MODE: BATCH_PRODUCTION_WITH_FINAL_HUMAN_REVIEW
PHASE: 1
DEADLINE: 5_WORKING_DAYS

OBJECTIVE:
Automatically discover and download large volumes of non-Portuguese videos from
Bilibili, Douyin, YouTube, TikTok, Facebook, and Kwai; select high-value segments;
create substantially re-edited pt-BR short videos with new narration, subtitles,
and JaguarTV branding; then output publication-ready packages for human review.

PHASE_1_EXCLUDES:
- User/admin/role management.
- Social account management.
- Automatic publishing.
- Analytics and CRM.

P0_PIPELINE:
DISCOVER -> LANGUAGE_FILTER -> SCORE -> DEDUPE -> DOWNLOAD -> ASR -> SCENE_ANALYSIS
-> HIGHLIGHT_SELECTION -> PTBR_REWRITE -> PTBR_TTS -> SUBTITLE_ALIGNMENT
-> 9X16_EDIT -> BRAND_RENDER -> AUTO_QA -> READY_FOR_REVIEW

TARGET_PER_RUN:
- Discover 500 candidates.
- Download top 50.
- Produce top 10.
- Output 20-60 second 1080x1920 H.264/AAC videos.

EDIT_RULE:
Do not translate the full source unchanged. Create a new pt-BR hook, context,
narrative order, commentary, conclusion, subtitles, and JaguarTV end card. Select
only the source segments required by the new narrative.

FAILURE_RULE:
One platform failure must not stop other adapters. Retry each step at most twice.
Persist intermediate assets and resume from the last successful state.

DONE:
A job is complete only when video.mp4, cover.jpg, metadata.json, source preview,
manifest, and review.json exist under ready_for_review/<job_id>/.
```
