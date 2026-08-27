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
./scripts/install-external-integrations.sh --name krillinai
```

同步器会拒绝覆盖非 Git 目录、来源地址不匹配的仓库或有未提交改动的检出。它只检出源码；各工具依赖、账号和 Cookie 仍按上游文档在运行环境中配置。

## 工作流映射

| 工具 | 许可证 | 在本项目中的入口 |
| --- | --- | --- |
| MediaCrawler | 上游声明包含商业使用限制，生产使用前需完成许可确认 | 负责抖音、快手、B站和小红书发现；JSONL 通过 `jaguartv ingest-mediacrawler` 进入统一候选库 |
| Agent Reach | MIT | 发现路由和运行健康检查；标准化 JSONL 通过 `jaguartv ingest-agent-reach` 入库 |
| Scrapling | BSD-3-Clause | 每日北京时间 05:00 抓取 Google Trends RSS，与 pytrends 结果合并、去重并保留来源 |
| f2 | Apache-2.0 | 独立 editable 环境安装完整 CLI；抖音候选唯一下载器，登录态仅通过权限为 0600 的临时配置传入 |
| yt-dlp | Unlicense | YouTube、TikTok、Facebook、X、Instagram 的统一下载器；Kwai 先解析官方页面 CDN，再交给 yt-dlp 下载 |
| KrillinAI | GPL-3.0 | 必需的独立 CLI；执行转录、pt-BR 逐句翻译和逐段 TTS，固定提交后由 `scripts/install-krillinai.sh` 构建 |

GPL 项目保持独立进程或独立检出，不与本仓库源码打包。KrillinAI 的私密 `config/config.toml` 不进入 Git；它可以切换转录、翻译模型和 TTS 供应商。使用和再分发前应复核对应固定提交中的上游许可证；`NOASSERTION` 项目不应在未确认许可时重新分发。

`jaguartv f2-status` 会分别探测抖音与 TikTok 模块。f2 0.0.1.7 的 TikTok 模块在部分网络环境会在导入阶段因 `msToken` 获取失败而不可用，因此当前生产 TikTok 下载继续使用 yt-dlp；该故障不会影响 f2 的抖音下载，也不会拖垮工厂进程。

## Codex Skill 路由

仓库自带 `.agents/skills/jaguartv-content-factory`，它是统一操作入口。保留的 Agent skill 及路由记录在 `config/agent-skills.yaml`：

- 发现和下载结果必须进入 `ingest`、`ingest-mediacrawler` 或 `upload`，不能建立第二套候选数据库。
- KrillinAI 的转录、翻译、字幕和 TTS 结果必须回到候选 ID 对应的工作目录，再由 `produce` 生成审核包。
- 工厂保留原视频画面，不执行 OCR、烧录字幕模糊或宣传尾卡裁剪。
- Skill 不负责自动发布；`workspace/ready_for_review/` 和服务器审核页仍是人工审核边界。

这些 skill 是给 Agent 阅读的操作方法，不是 Python 库，也不会被 worker 自动调用。仓库只把已选规则固化进 Python/Remotion 流程；实际执行证据仍以 CLI、API、数据库记录和 worker 日志为准。

## 数据边界

Git 包含代码、SQLite 建表/迁移逻辑、配置、示例数据、品牌资产和固定版本清单。以下内容只留在 `workspace/` 或服务器 `.env`：运行数据库、源视频、成片、Cookie、登录会话、访问令牌和第三方仓库检出。这样可避免泄露账号凭据，也避免 GitHub 文件大小限制影响仓库克隆和部署。
