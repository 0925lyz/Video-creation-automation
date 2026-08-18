# YouTube 自动发布接口包

本目录是 JaguarTV Content Factory 的 YouTube 自动发布接口交付包。它不包含任何真实密钥、refresh token、cookie 或频道私有凭据。

## 已验证能力

- Google OAuth Web Server 授权入口。
- `/oauth/youtube/start?account=consumer_football` 生成授权链接。
- `/oauth/youtube/callback` 接收 Google code 并换取 refresh token。
- refresh token 使用服务器本地 `JAGUARTV_OAUTH_TOKEN_KEY` 加密后写入 SQLite。
- 授权后读取实际 YouTube Channel ID，避免只靠邮箱或频道名识别。
- 审核通过视频可用 YouTube Data API `videos.insert` 做 `private` 私密上传测试。
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
| `server_integration.md` | 接入现有 Dashboard 和服务器的步骤 |

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
7. 打开授权入口：

```text
https://factory.jarg.top/oauth/youtube/start?account=consumer_football
```

8. 用拥有目标频道权限的 Google 账号完成授权。
9. 页面显示授权成功后，再做 private 私密测试上传。

## 账号映射

足球类标签对应：

```text
足球类 / 足球球星 -> consumer_football -> jaguartv vivo
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
- 自动上传先使用 `private`，稳定后再考虑 `unlisted` 或公开。
