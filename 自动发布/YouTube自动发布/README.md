# YouTube 自动发布

本目录是 JaguarTV Content Factory 的“自动文案生成 + YouTube 自动发布”完整交付包，集中保存接口实现、执行说明、配置模板、上线检查清单和常驻 worker 示例。它不包含任何真实密钥、refresh token、cookie 或频道私有凭据。

## 核心目标

- 审核通过后自动生成 YouTube 发布任务。
- 根据爬取分类标签、关键词、源视频原标题和源文案生成 pt-BR 标题钩子、文案和 5 个标签。
- 将源标题和源文案作为 JSON 不可执行素材传给大模型，降低 prompt injection 风险。
- YouTube 说明中固定追加 `tags relacionadas` 相关标签段。
- 根据内容分类选择 YouTube 账号。
- 按账号发布时段和每日上限排程。
- 发布 worker 到点上传视频到 YouTube。
- 发布成功后回写 `publications`、`candidates.published_flag` 和事件流水。
- 仪表盘在审核通过状态下显示已发布到哪个 YouTube 账号。

## 已验证能力

- Google OAuth Web Server 授权入口。
- `/oauth/youtube/start?account=consumer_football` 生成授权链接。
- `/oauth/youtube/callback` 接收 Google code 并换取 refresh token。
- refresh token 使用服务器本地 `JAGUARTV_OAUTH_TOKEN_KEY` 加密后写入 SQLite。
- 授权后读取实际 YouTube Channel ID，避免只靠邮箱或频道名识别。
- 审核通过视频可用 YouTube Data API `videos.insert` 做 `private` 私密上传测试。
- 2026-08-17 已完成一次 `jaguartv_vivo` 公开上传测试，YouTube 返回视频 ID。
- 发布结果可回写 `publications`、`events` 和候选 `published_flag`。

2026-08-15 已在服务器完成一次私密测试：

- 账号配置：`consumer_football`
- 频道：`jaguartv vivo`
- 源平台：Facebook
- 隐私状态：private
- YouTube 处理状态：processed / succeeded

## 文件说明

| 文件 | 作用 |
|---|---|
| `.env.example` | 服务器环境变量模板，不含真实密钥 |
| `schema.sql` | YouTube 授权、发布记录、事件记录所需 SQLite schema |
| `youtube_auto_publish.py` | OAuth 授权、token 加密、频道校验、私密上传的可执行实现 |
| `publishing_copywriter.py` | 自动发布文案生成、Doubao prompt、输出解析和说明标签拼接 |
| `server_integration.md` | 接入现有 Dashboard 和服务器的步骤 |
| `执行说明书.md` | 从配置、授权、审核、排队、发布到排障的完整操作说明 |
| `上线验收清单.md` | 部署前后必须核对的项目 |
| `流程图.md` | 自动发布链路流程图 |
| `代码清单.md` | 自动发布相关代码入口和职责清单 |
| `templates/env.example` | OAuth 和运行时环境变量模板 |
| `templates/pipeline-publishing.example.yaml` | 发布账号和排程配置模板 |
| `systemd/jaguartv-youtube-publish-worker.service.example` | 生产环境常驻发布 worker 模板 |

## 相关代码入口

- `src/jaguartv_factory/publisher.py`
- `src/jaguartv_factory/publishing_copywriter.py`
- `src/jaguartv_factory/publish_worker.py`
- `src/jaguartv_factory/youtube_publisher.py`
- `src/jaguartv_factory/cli.py`
- `src/jaguartv_factory/dashboard.py`
- `src/jaguartv_factory/core.py`
- `src/jaguartv_factory/web/app.js`
- `src/jaguartv_factory/web/styles.css`

## 基本流程

1. 在 Google Cloud 创建项目。
2. 启用 YouTube Data API v3。
3. 配置 OAuth consent screen。
4. 创建 Web application OAuth Client。
5. 在 Google Cloud 填入：

```text
Authorized JavaScript origins:
https://factory.jarg.top

Authorized redirect URIs:
https://factory.jarg.top/oauth/youtube/callback
```

6. 在服务器 `.env` 配置 OAuth 变量。
7. 从同一台线上 Dashboard 生成授权入口，不要用本地生成的 `state` 回调线上服务：

```text
https://factory.jarg.top/oauth/youtube/start?account=jaguartv_vivo
```

8. 用拥有目标频道权限的 Google 账号完成授权。
9. 页面显示授权成功后，先用 `publish-worker --once --dry-run` 检查到期队列，再做测试上传。

## 账号映射

足球类标签对应：

```text
足球类 / 足球球星 -> jaguartv_vivo -> jaguartv vivo
```

内容来源限制：

```text
发布到 YouTube 的 consumer_main / consumer_football / consumer_entertainment 内容，不允许源平台是 YouTube。
```

## 安全要求

- 不把 `JAGUARTV_GOOGLE_CLIENT_SECRET` 写进 Git。
- 不把 refresh token 写进 Git、日志或前端页面。
- 服务器 `.env` 权限应为 `600`。
- refresh token 只保存加密值。
- `JAGUARTV_OAUTH_TOKEN_KEY` 用于加密 refresh token，授权后不能随意更换。
- `JAGUARTV_OAUTH_STATE_SECRET` 用于签名 OAuth state，授权链接必须由接收回调的同一台服务生成。
- 自动上传可先使用 `private`，稳定后再按账号配置使用 `unlisted` 或 `public`。
