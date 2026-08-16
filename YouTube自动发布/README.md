# YouTube 自动发布交付包

这个目录打包 JaguarTV Content Factory 的 YouTube 自动发布功能说明、配置模板和上线检查清单。

核心目标：

- 审核通过后自动生成 YouTube 发布任务。
- 根据内容分类选择 YouTube 账号。
- 按账号发布时段和每日上限排程。
- 发布 worker 到点上传视频到 YouTube。
- 发布成功后回写 `publications`、`candidates.published_flag` 和事件流水。
- 仪表盘在审核通过状态下显示已发布到哪个 YouTube 账号。

目录内容：

- `执行说明书.md`：从配置、授权、审核、排队、发布到排障的完整操作说明。
- `上线验收清单.md`：部署前后必须核对的项目。
- `templates/env.example`：OAuth 和运行时环境变量模板，不包含任何真实密钥。
- `templates/pipeline-publishing.example.yaml`：发布账号和排程配置模板。
- `systemd/jaguartv-youtube-publish-worker.service.example`：生产环境常驻发布 worker 模板。

相关代码入口：

- `src/jaguartv_factory/publisher.py`
- `src/jaguartv_factory/publish_worker.py`
- `src/jaguartv_factory/youtube_publisher.py`
- `src/jaguartv_factory/cli.py`
- `src/jaguartv_factory/dashboard.py`
- `src/jaguartv_factory/core.py`
- `src/jaguartv_factory/web/app.js`
- `src/jaguartv_factory/web/styles.css`

安全原则：

- 不提交 Google OAuth Client Secret、refresh token、Cookie、服务器 SSH key。
- YouTube 来源视频默认不再发布到受限的消费类 YouTube 账号，避免平台内搬运风险。
- 自动发布仍以人工审核通过作为边界，只有 `APPROVED` 候选才会进入队列。
