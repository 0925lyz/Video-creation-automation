# JaguarTV Content Factory vNEXT

面向巴西葡语市场的视频内容工厂：多平台候选入库、长视频智能精彩切片、按内容类型选择音频策略、可选 Reaction 合成、JaguarTV 品牌渲染、服务器审核包和人工审核。

所有运行时视频都保存在 `factory.jarg.top` 对应服务器。源素材和 Reaction 输入默认私有，只有审核包通过 `/media/review/...` 提供访问；项目不使用办公协作盘作为视频存储。

## 核心能力

- 内容分类：足球、体育集锦、舞蹈音乐、搞笑生活、动画少儿、肥皂剧、解说、未知。
- 智能切片：音频峰值 35% + 运动 25% + 镜头变化 20% + 关键词 15% + 回放 5%。
- Reaction：画中画、上下分屏、左右分屏；Reaction 不覆盖品牌尾卡。
- 音频策略：源音 + Funk、源音乐优先、仅 Funk、保留葡语、葡语配音字幕、BGM only。
- 合规门禁：未核验权利状态默认 `BLOCKED_RIGHTS`。
- 可解释审核：保存内容类型、命中规则、片段时间、精彩度、原因、Reaction 和音频参数。

## 本地启动

```bash
./scripts/bootstrap.sh
.venv/bin/jaguartv doctor
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

## 腾讯云部署

- 首次安装：`scripts/server-install.sh`
- 后续同步：`scripts/server-sync.sh`
- systemd 默认服务：`jaguartv-content-factory-vnext`
- 默认目录：`/opt/jaguartv-content-factory-vnext`
- 线上入口：[factory.jarg.top](https://factory.jarg.top/)

首次安装会生成 `JAGUARTV_EVENTS_TOKEN` 和 `JAGUARTV_UPLOAD_TOKEN`，只写入服务器 `.env`，不进入 Git。

## 文档

- [产品需求文档](docs/JaguarTV_vNEXT_产品需求文档.md)
- [双工具评估与融合实施报告](docs/双工具评估与融合实施报告.md)
- [WorkBuddy 执行总指令](prompts/WORKBUDDY_VNEXT_SYSTEM_PROMPT.md)

## 合规边界

体育赛事、影视/短剧和平台用户原创内容属于高风险素材。语言替换、字幕模糊、加入 Funk 或 Reaction 都不会自动取得版权。生产环境只处理自有、已授权、公共领域或许可明确允许商业改编的素材。禁止为规避版权检测开发去水印、镜像、变速或指纹扰动功能。
