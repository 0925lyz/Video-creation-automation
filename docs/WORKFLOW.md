# 当前唯一工作流

本仓库以 `jaguartv` CLI、Dashboard API、后台 worker 和 `workspace/factory.db` 为同一套系统。`.agents/skills/jaguartv-content-factory/scripts/factory.sh` 是 AI Agent 的统一入口；skill 负责指导 Agent，真正执行下载、数据库写入、渲染和发布的是 Python 服务。

## 视频闭环

1. 北京时间每日 05:00 运行 pytrends；失败时由 Scrapling 读取 Google Trends RSS，再写入数据库和 `workspace/runtime/keywords.trends.yaml`。
2. Agent Reach 与 MediaCrawler 的标准化结果、内置平台适配器、浏览器会话及手动导入统一进入候选库，并保留关键词、分类、源标题和源标签。
3. `yt-dlp` 下载 YouTube、TikTok、Facebook、X、Instagram、Kwai，`f2` 只下载抖音；B站和小红书沿用独立适配器。源媒体进入 `workspace/jobs/<candidate_id>/`，Cookie 和登录会话只保存在服务器私有目录。
4. 制作服务执行尾卡检测、智能切片、OCR、pt-BR 本地化、配音和音频策略，再用 Remotion 生成通用版和 FB 版。
5. 自动制作必须通过双版本、媒体流、路径、哈希和渲染任务门禁，才能进入 `READY_FOR_REVIEW`。管理员也可以把已经制作完成的外部成片作为“导入成片”直接批准，这类记录会明确标记 `external_import`，不冒充自动二创。
6. 人工批准后才能建立发布任务。YouTube 和 X 支持自动发布；TikTok、Facebook、抖音、B站、Kwai、Instagram 当前只生成文案并下载本地成片，不声称自动发布成功。
7. YouTube worker 回收公开状态和数据，Dashboard 汇总关键词、文案和转化归因。反馈目前生成建议，不会未经审核自动改写生产策略。

## 独立海报库存

海报不参与视频下载、切片或 Remotion 成片链路，但 Dashboard 保留独立库存，用于导入、分类、筛选、人工审核、预览和下载。海报记录与文件继续使用服务器现有存储，不与视频候选状态混用。

## 运行入口

```bash
./.agents/skills/jaguartv-content-factory/scripts/factory.sh doctor
./.agents/skills/jaguartv-content-factory/scripts/factory.sh discover --platform youtube --limit 3
./.agents/skills/jaguartv-content-factory/scripts/factory.sh download --candidate <id>
./.agents/skills/jaguartv-content-factory/scripts/factory.sh produce --candidate <id>
./.agents/skills/jaguartv-content-factory/scripts/factory.sh review
./.agents/skills/jaguartv-content-factory/scripts/factory.sh ui --host 127.0.0.1 --port 8787
```

Dashboard 默认需要 `JAGUARTV_DASHBOARD_TOKEN`。浏览器访问 `/login`，将它作为“访问密码”登录；程序调用仍可通过 `X-Dashboard-Token` 或 Bearer Token 认证，不要把 Token 放在 URL 中。`JAGUARTV_DASHBOARD_PUBLIC=1` 只开放只读页面；写操作仍需管理员认证。上传使用独立的 `JAGUARTV_UPLOAD_TOKEN`。
