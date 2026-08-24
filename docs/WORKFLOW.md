# 当前唯一工作流

本仓库以 `jaguartv` CLI、Dashboard API、后台 worker 和 `workspace/factory.db` 为同一套系统。`.agents/skills/jaguartv-content-factory/scripts/factory.sh` 是 AI Agent 的统一入口；skill 负责指导 Agent，真正执行下载、数据库写入、渲染和发布的是 Python 服务。

## 视频闭环

1. Google Trends 每日热点写入数据库和 `workspace/runtime/keywords.trends.yaml`，发现任务把它与 `config/keywords.brazil.yaml` 合并。
2. 内置平台适配器、浏览器会话、手动 URL/文件导入或 MediaCrawler JSONL 把素材写入统一候选库。
3. 下载器把源媒体放入 `workspace/jobs/<candidate_id>/`；Cookie 和登录会话只在 `workspace/` 或服务器环境中保存。
4. 制作服务执行尾卡检测、智能切片、OCR、pt-BR 本地化、配音和音频策略，再用 Remotion 生成通用版和 FB 版。
5. 自动制作必须通过双版本、媒体流、路径、哈希和渲染任务门禁，才能进入 `READY_FOR_REVIEW`。管理员也可以把已经制作完成的外部成片作为“导入成片”直接批准，这类记录会明确标记 `external_import`，不冒充自动二创。
6. 人工批准后才能建立发布任务。YouTube 和 X 支持自动发布；TikTok、Facebook、抖音、B站、Kwai、Instagram 当前只生成文案并下载本地成片，不声称自动发布成功。
7. YouTube worker 回收公开状态和数据，Dashboard 汇总关键词、文案和转化归因。反馈目前生成建议，不会未经审核自动改写生产策略。

## 海报流程

当前正式代码提供海报导入、附件、预览、审批、下载、删除和库存管理。仓库没有通用的球赛预测海报生成器；历史上的按场次硬编码脚本保存在归档分支，不属于生产能力。

## 运行入口

```bash
./.agents/skills/jaguartv-content-factory/scripts/factory.sh doctor
./.agents/skills/jaguartv-content-factory/scripts/factory.sh discover --platform youtube --limit 3
./.agents/skills/jaguartv-content-factory/scripts/factory.sh download --candidate <id>
./.agents/skills/jaguartv-content-factory/scripts/factory.sh produce --candidate <id>
./.agents/skills/jaguartv-content-factory/scripts/factory.sh review
./.agents/skills/jaguartv-content-factory/scripts/factory.sh ui --host 127.0.0.1 --port 8787
```

Dashboard 默认需要 `JAGUARTV_DASHBOARD_TOKEN`。`JAGUARTV_DASHBOARD_PUBLIC=1` 只开放只读页面；写操作仍需管理员令牌。上传使用独立的 `JAGUARTV_UPLOAD_TOKEN`。
