# 服务器接入说明

## 1. 环境变量

把 `.env.example` 中的变量复制到服务器项目目录 `.env`，真实值只放服务器。

必填：

```text
JAGUARTV_PUBLIC_BASE_URL=https://factory.jarg.top
JAGUARTV_GOOGLE_REDIRECT_URI=https://factory.jarg.top/oauth/youtube/callback
JAGUARTV_GOOGLE_CLIENT_ID=...
JAGUARTV_GOOGLE_CLIENT_SECRET=...
JAGUARTV_OAUTH_STATE_SECRET=...
JAGUARTV_OAUTH_TOKEN_KEY=...
```

生成本地密钥：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

## 2. Google Cloud OAuth Client

OAuth Client 类型选择 `Web application`。

```text
Authorized JavaScript origins:
https://factory.jarg.top

Authorized redirect URIs:
https://factory.jarg.top/oauth/youtube/callback
```

## 3. Dashboard 路由

现有 Dashboard 需要接入两个 GET 路由：

```text
GET /oauth/youtube/start
GET /oauth/youtube/callback
```

`start` 调用 `youtube_oauth_start_url(config, account)` 后 302 跳转 Google。

`callback` 调用 `save_youtube_oauth_callback(config, query)`，成功后显示账号、频道名和 Channel ID。

## 4. 授权 consumer_football

打开：

```text
https://factory.jarg.top/oauth/youtube/start?account=consumer_football
```

用拥有 `jaguartv vivo` 权限的 Google 账号授权。成功后应显示：

```text
账号配置：consumer_football
频道：jaguartv vivo
Channel ID：UC...
refresh token 已加密保存
```

## 5. 私密测试上传

先使用 `private`，不要直接公开。示例：

```bash
python3 自动发布/YouTube自动发布/youtube_auto_publish.py private-upload \
  --db workspace/factory.db \
  --candidate 91c5f92e23dde739 \
  --account consumer_football \
  --video workspace/ready_for_review/91c5f92e23dde739/0813-Facebook-8-通用版.mp4 \
  --title "Private test - JaguarTV Futebol" \
  --description "Private OAuth upload test. Source platform: Facebook." \
  --source-platform facebook
```

## 6. 上线前检查

- 候选状态必须是 `APPROVED`。
- 源平台不能是 YouTube。
- 目标账号必须已授权。
- 默认隐私状态先用 `private`。
- 上传成功后必须写回 `publications.post_url`。
- 重试前先检查是否已有 `PUBLISHED` 记录。
