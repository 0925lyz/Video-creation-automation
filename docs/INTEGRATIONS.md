# 外部仓库与 Skill 集成

本项目把第三方工具作为独立运行时依赖接入，不复制其源码到主仓库。固定版本、检出目录、许可证和工作流入口统一记录在 `config/integrations.yaml`；检出内容位于 `workspace/external_tools/`，不会进入 Git。

## 同步与检查

```bash
# 查看所有外部工具状态，不访问网络
.venv/bin/jaguartv integrations

# 按清单同步全部仓库到固定提交
.venv/bin/jaguartv integrations --sync

# 只同步一个工具
.venv/bin/jaguartv integrations --sync --name mediacrawler

# 等价的项目脚本
./scripts/install-external-integrations.sh --name pyvideotrans
```

同步器会拒绝覆盖非 Git 目录、来源地址不匹配的仓库或有未提交改动的检出。它只检出源码；各工具依赖、账号和 Cookie 仍按上游文档在运行环境中配置。

## 工作流映射

| 工具 | 许可证 | 在本项目中的入口 |
| --- | --- | --- |
| MediaCrawler | 上游未提供标准 SPDX 声明 | JSONL 结果通过 `jaguartv ingest-mediacrawler` 进入统一候选库 |
| Douyin_TikTok_Download_API | Apache-2.0 | 可选抖音解析服务；地址由 `sources.adapters.douyin.api_base` 配置 |
| XHS-Downloader | GPL-3.0 | 可选小红书解析服务；通过 HTTP 与主进程隔离 |
| Scrapling | BSD-3-Clause | 可选浏览器检索工具；内置 Playwright 回退仍是默认实现 |
| PaddleOCR | Apache-2.0 | 可选 OCR 后端，由 `edit.ocr_backend` 选择 |
| pyvideotrans | GPL-3.0 | 可选 STT、字幕翻译、TTS 和整段翻译适配器，各能力默认关闭 |

GPL 项目保持独立进程或独立检出，不与本仓库源码打包。使用和再分发前应复核对应固定提交中的上游许可证；`NOASSERTION` 项目不应在未确认许可时重新分发。

## Codex Skill 路由

仓库自带 `.agents/skills/jaguartv-content-factory`，它是统一操作入口。`config/integrations.yaml` 同时记录当前工作流可调用的可选 Skill：

- 发现和下载结果必须进入 `ingest`、`ingest-mediacrawler` 或 `upload`，不能建立第二套候选数据库。
- 音视频理解、剪辑、字幕和 TTS 结果必须回到候选 ID 对应的工作目录，再由 `produce` 生成审核包。
- 去除文字、水印或遮挡的能力仅用于自有或明确授权素材，不能用于规避平台或版权识别。
- Skill 不负责自动发布；`workspace/ready_for_review/` 和服务器审核页仍是人工审核边界。

这些 Skill 是操作环境的可选能力，不属于本仓库 Python 依赖，也不会随项目压缩包复制其实现。

## 数据边界

Git 包含代码、SQLite 建表/迁移逻辑、配置、示例数据、品牌资产和固定版本清单。以下内容只留在 `workspace/` 或服务器 `.env`：运行数据库、源视频、成片、Cookie、登录会话、访问令牌和第三方仓库检出。这样可避免泄露账号凭据，也避免 GitHub 文件大小限制影响仓库克隆和部署。
