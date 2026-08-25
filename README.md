# JaguarTV Content Factory vNEXT

面向巴西葡语市场的视频内容工厂：巴西热点关键词、多平台候选入库、长视频智能切片、pt-BR 本地化、Remotion 双版本渲染、人工审核、发布文案、YouTube/X 发布和发布后数据回收。Dashboard 同时保留独立海报库存，用于海报导入、筛选、审核、预览和下载。

所有运行时视频都保存在 `factory.jarg.top` 对应服务器。源素材和 Reaction 输入默认私有，只有审核包通过 `/media/review/...` 提供访问；项目不使用办公协作盘作为视频存储。

## 核心能力

- 内容分类：足球、体育集锦、舞蹈音乐、搞笑生活、动画少儿、肥皂剧、解说、未知。
- 智能切片：音频峰值 35% + 运动 25% + 镜头变化 20% + 关键词 15% + 回放 5%。
- Reaction：画中画、上下分屏、左右分屏；Reaction 不覆盖品牌尾卡。
- 音频策略：源音 + Funk、源音乐优先、仅 Funk、保留葡语、葡语配音字幕、BGM only。
- 人工审核交接：系统记录权利状态和风险等级，不在制作前自动阻断；内部人员在服务器审核页确认授权与发布范围。
- 可解释审核：保存内容类型、命中规则、片段时间、精彩度、原因、Reaction 和音频参数。
- 发布闭环：YouTube/X 支持审核后发布；其他平台当前只生成文案和本地成片，不假装已自动发布。
- 热点反馈：每日热点进入运行时关键词层，不会再把服务器 Git 配置改脏；数据分析只生成优化建议，需审核后应用。

## 发布文案 AI

审核页发布弹窗使用 OpenAI Responses API 生成巴西葡语标题和标签，默认模型为 `gpt-5.6-terra`。服务端执行结构化输出校验和最多三次短重试；AI 未配置或暂时不可用时回退到来源关键词规则，发布按钮不会因此失效。

生产服务器只需在私有 `.env` 中设置 `JAGUARTV_PUBLISHING_AI_API_KEY`。可选的 `JAGUARTV_PUBLISHING_AI_MODEL` 和 `JAGUARTV_OPENAI_BASE_URL` 分别覆盖模型和兼容 API 地址。密钥不得写入 Git。YouTube 弹窗只显示“标题文案”和“说明标签”；说明标签最终进入 YouTube 说明框，隐藏的通用“文案标签”保持空白。

## 分类关键词维护

- `scripts/import_daily_keywords.py`：每天读取 `workspace/runtime/daily_keywords.txt`，按“分类：关键词1、关键词2”格式导入。
- `scripts/carry_forward_tag_keywords.py`：当天没有分类关键词时，继承最近一天的数据。
- `scripts/clear_tag_keywords.py`：每两天清空一次 `daily_keywords:*`，清空前先备份数据库；Google Trends 数据不受影响。
- `scripts/install-keyword-maintenance.sh`：由服务器安装和同步脚本自动安装并启用上述 systemd 定时任务。

## 本地启动

```bash
./scripts/bootstrap.sh
.venv/bin/jaguartv doctor
.venv/bin/jaguartv ytdlp-status
.venv/bin/jaguartv ui --host 127.0.0.1 --port 8787
```

打开 `http://127.0.0.1:8787/`。

## CLI 工作流

```bash
# 发现、下载
.venv/bin/jaguartv discover --platform youtube --limit 3
.venv/bin/jaguartv ingest-mediacrawler \
  /path/MediaCrawler/data/douyin/jsonl/search_contents_2026-07-27.jsonl \
  --min-likes 2000
.venv/bin/jaguartv ingest-agent-reach \
  /path/agent-reach/x/results.jsonl --platform x --min-views 2000
.venv/bin/jaguartv list --status DISCOVERED
.venv/bin/jaguartv download --candidate <id>

# 只分析长视频，不渲染
.venv/bin/jaguartv analyze \
  --candidate <id> \
  --content-type football \
  --segment-strategy sports_highlight \
  --max-segments 3 \
  --max-duration 30

# 上传 Reaction 到服务器私有资产区
.venv/bin/jaguartv upload /path/reaction.mp4 --kind reaction

# 智能切片 + Reaction 制作
.venv/bin/jaguartv produce \
  --candidate <id> \
  --content-type football \
  --segment-strategy sports_highlight \
  --audio-policy source_plus_funk \
  --max-segments 3 \
  --max-duration 30 \
  --reaction-mode picture_in_picture \
  --reaction-source /absolute/server/path/reaction.mp4 \
  --rights-status LICENSED
```

## 服务器存储

默认结构：

```text
workspace/server_media/
  uploads/
    source/       # 私有，不经 /media 提供
    reaction/     # 私有，不经 /media 提供
  review/
    <package_id>/
      video.mp4
      cover.jpg
      metadata.json
      review.json
```

浏览器上传接口：

```text
POST /api/uploads?kind=reaction&filename=reaction.mp4
Content-Type: application/octet-stream
X-Upload-Token: <JAGUARTV_UPLOAD_TOKEN>
```

最大上传大小默认 2GB。服务端流式写入 `.uploading` 临时文件，完整接收后原子重命名。

## 双版本审核成片

Remotion 品牌模式会为每个审核包生成两个版本：

- `通用版`：播放期间左上角显示图一、右上角显示图二，结尾追加宣传尾图；竖屏使用绿图，横屏使用蓝图。
- `FB版`：不加角标、不加宣传尾图，只保留归一化后的成片，方便 Facebook 单独发布。

默认预览文件仍是 `review/<candidate>/video.mp4`。内容库存页会读取同一审核包内的全部 `.mp4`，为 `通用版` 和 `FB版` 分别显示“预览 / 下载成片 / 服务器成片”。

发布数据回传接口：

```text
POST /api/callback
Content-Type: application/json

{
  "candidate_id": "candidate-or-package-id",
  "publisher": "operator-name",
  "platform": "youtube",
  "views": 1000,
  "clicks": 30,
  "registrations": 4
}
```

今日巴西热词接口：

```text
GET /api/hot-keywords?date=today
```

## 腾讯云部署

- 首次安装：`scripts/server-install.sh`
- 后续同步：`scripts/server-sync.sh`
- systemd 默认服务：`jaguartv-content-factory-vnext`
- 默认目录：`/opt/jaguartv-content-factory-vnext`
- 默认 GitHub 仓库：`https://github.com/0925lyz/Video-creation-automation.git`
- 当前服务器内部监听：`127.0.0.1:8788`，由 Nginx 对外提供 HTTPS
- 线上入口：[factory.jarg.top](https://factory.jarg.top/)

首次安装会生成 `JAGUARTV_EVENTS_TOKEN` 和 `JAGUARTV_UPLOAD_TOKEN`，只写入服务器 `.env`，不进入 Git。

## 文档

- [当前唯一工作流](docs/WORKFLOW.md)
- [外部仓库和 Agent Skill](docs/INTEGRATIONS.md)

## 合规边界

体育赛事、影视/短剧和平台用户原创内容属于高风险素材。语言替换、字幕模糊、加入 Funk 或 Reaction 都不会自动取得版权。生产环境只处理自有、已授权、公共领域或许可明确允许商业改编的素材。禁止为规避版权检测开发去水印、镜像、变速或指纹扰动功能。
