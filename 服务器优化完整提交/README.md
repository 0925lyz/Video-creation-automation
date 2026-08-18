# 服务器优化完整提交

本目录是 `factory.jarg.top` 生产环境的可审计归档，初始生成时间为 2026-08-14，并在 2026-08-15 补充当前线上可用的服务器功能代码。它同时保存服务器代码、脱敏数据、部署配置、外部仓库版本和 Codex 操作 Skill 清单，用于迁移、审查和继续开发。

## 归档范围

- `server-snapshot/`：从服务器 `/opt/jaguartv-content-factory-vnext` 同步的代码、配置、网页、Remotion 模板、脚本、测试和品牌素材。
- `data/`：生产 SQLite 的脱敏副本、结构 SQL、聚合统计和匿名文件库存。原始数据库不会进入 Git。
- `deployment/`：服务器实际 Nginx、systemd 和运行时版本的脱敏快照。
- `integrations/`：外部 GitHub 仓库、Python/npm 依赖和 Codex Skills 的版本账本。
- `third_party/`：外部仓库的本地检出位置，默认不提交；由锁文件和拉取脚本按精确 SHA 重建。
- `tools/`：重新生成脱敏数据和校验归档安全性的工具。
- `VALIDATION.md`：归档验证结果、生产快照测试结果和已知失败项。
- `checksums.sha256`：除清单自身及本地构建缓存外，所有归档文件的 SHA-256。

## 生产快照

- 主机：`43.134.128.197`
- 项目路径：`/opt/jaguartv-content-factory-vnext`
- 服务器 Git HEAD：`402e69b`
- 数据库候选：643 条
- 工作区文件：15,886 个，总计约 21.98 GB
- 媒体文件：1,363 个视频/音频文件，只提交匿名清单，不提交二进制内容
- 服务器代码存在未提交生产补丁；这些补丁已完整保存在 `server-snapshot/`

## 最终功能

- 服务器端发现、下载、30 分钟时长限制、源视频去重和失败重试。
- B站、抖音、小红书、TikTok、YouTube、Facebook 平台适配及浏览器兜底。
- 多信号智能切片、父子切片归组、手动剪辑和生产任务恢复。
- 中文字幕动态 OCR、葡语字幕、可选 pyVideoTrans/PaddleOCR 适配和 Tesseract 回退。
- Remotion 双版本渲染、3:4 移动画布、品牌角标、完整尾图和文案设计。
- 服务器审核库存、下载登记、发布数据回传、删除、恢复、去重和最多四个标准输出。
- Google Trends 巴西每日同步、宣传文案生成器和独立 Copywriter 页面。

## 2026-08-15 补充

- 刷新 `server-snapshot/`，合入当前服务器库存、发现素材、公开上传、文案设计贴图、文案设计归档、输出标签和待审核流程代码。
- 保留 `data/`、`deployment/`、`integrations/`、`tools/` 的脱敏归档结构，不引入生产媒体、私钥、Cookie 或 `.env`。
- `checksums.sha256` 已随本次快照刷新重新生成。

## 数据安全边界

以下内容明确不进入仓库：

- `.env`、上传令牌、API Key、Cookie、浏览器 storage state、私钥和证书私钥。
- 原始生产数据库、下载人姓名、备注、账号、访问 URL 和服务器绝对路径。
- 下载素材、渲染视频、音频、模型缓存、虚拟环境和 `node_modules`。
- macOS 资源叉、Python 缓存、测试缓存和临时 PID/锁文件。

`data/factory.sanitized.db` 保留表结构和行关系，但候选 ID、源 ID、人员、URL、标题、路径和自由文本均已删除或确定性假名化。

## 验证

```bash
python3 服务器优化完整提交/tools/verify_archive.py
python3 -m sqlite3 服务器优化完整提交/data/factory.sanitized.db "PRAGMA integrity_check;"
shasum -a 256 -c 服务器优化完整提交/checksums.sha256
bash 服务器优化完整提交/integrations/fetch-external-repositories.sh
```

如需重新从生产数据库生成脱敏数据：

```bash
python3 服务器优化完整提交/tools/export_sanitized_snapshot.py \
  --database /secure/local-copy/factory.db \
  --media-list /secure/local-copy/workspace-files.tsv \
  --output-dir 服务器优化完整提交/data
```

## 恢复说明

这个归档可恢复代码、依赖版本、服务配置和数据库结构，但不会自动恢复生产凭据或 21 GB 媒体。部署时应重新创建 `.env`、SSL 私钥和平台登录态，并从受控对象存储或服务器备份恢复媒体。

当前 Nginx 快照反代到旧服务 `127.0.0.1:8787`，而 vNEXT systemd unit 监听 `127.0.0.1:8788`。迁移前必须明确选择一个生产入口并统一端口；本归档保留现状，不擅自修改线上路由。
