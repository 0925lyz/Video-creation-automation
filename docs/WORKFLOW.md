# 当前唯一工作流

本仓库以 `jaguartv` CLI、Dashboard API、后台 worker 和 `workspace/factory.db` 为同一套系统。`.agents/skills/jaguartv-content-factory/scripts/factory.sh` 是 AI Agent 的统一入口；skill 负责指导 Agent，真正执行下载、数据库写入、渲染和发布的是 Python 服务。

## 视频闭环

1. 圣保罗时间每日 00:10 运行 `jaguartv trends-run`：按服务器内容标签分别调用 Google Trends、`last30days-skill` 和 Agent Reach/Exa 搜索，合并后写入 `hot_keywords` 与 `workspace/runtime/keywords.trends.yaml`。
   自动热点跳过 `教程及优点展示类`、`官方性质类`、`合作类`、`运营教学类`、`教程及答疑类`。每日分类关键词文件 `workspace/runtime/daily_keywords.txt` 在 00:20 导入；当天没有新文件或新记录时，00:30 继承最近一天的分类关键词。`daily_keywords:*` 每两天清空一次并先备份数据库，多源自动热点来源不受清理影响。
2. Agent Reach 与 MediaCrawler 的标准化结果、内置平台适配器、浏览器会话及手动导入统一进入候选库，并保留关键词、分类、源标题和源标签。
3. 候选素材必须能确认时长且不超过 15 分钟；超过或时长未知的发现结果不入下载队列。`yt-dlp` 下载 YouTube、TikTok、Facebook、X、Instagram、Kwai，`f2` 只下载抖音；B站和小红书沿用独立适配器。下载前再次检查元数据，下载后再用 `ffprobe` 检查真实文件，超过 15 分钟的文件立即移除并标记 `TOO_LONG`。源媒体进入 `workspace/jobs/<candidate_id>/`，Cookie 和登录会话只保存在服务器私有目录。
4. 制作服务直接分析原视频，不再检查或裁剪宣传尾卡，不判断 B站/抖音中文内容，也不执行 OCR、中文字幕区域识别、裁剪或模糊。智能切片使用 `segment_overlap_sec: 3` 控制片段之间最多重叠 3 秒；没有合格片段就进入失败/人工处理，不拿原片冒充切片。
5. 固定提交的 `krillinai/KrillinAI` CLI 负责“语音转字幕 → 逐句翻译为巴西葡语 → 按片段生成配音”。转录、翻译模型和 TTS 供应商由服务器私密配置切换，每个任务还可单独指定音色。当前服务器由 KrillinAI 调用本地 `fasterwhisper/tiny` 转录；翻译请求经过任务生命周期内临时启动、仅监听 `127.0.0.1` 的协议桥转给现有 Responses API；配音使用固定版本的官方 `edge-tts`。转录和 TTS 供应商仍可改为 KrillinAI 支持的其他选项。任何一步失败都会停止制作，系统不再退回 pyvideotrans、Google 非正式翻译、MyMemory、系统语音或通用足球文案。
6. Remotion 把最多两行紧凑字幕放在真实视频画面的安全区域内，只生成一个通用版。正文不再添加固定底部宣传条；结尾从人工导入的 CTA 库随机选择与源视频横竖方向一致的素材，视频按原时长播放，图片显示 2 秒。字幕不会跟随 OCR 区域移动，也不会使用截图中那种横跨大半画面的左下黑框。
7. 自动制作的通用版必须通过媒体流、路径、哈希、CTA 记录、渲染任务以及黑屏、绿屏、花屏/解码异常、冻结画面门禁，才能进入 `READY_FOR_REVIEW`。待审核阶段可以删除一个正文中间片段并合并前后内容，也可按真实画布坐标添加文案和图片；编辑结果原位替换通用版，CTA 区域不叠加文案设计。管理员也可以把已经制作完成的外部成片作为“导入成片”直接批准，这类记录会明确标记 `external_import`，不冒充自动二创。
8. 人工批准后才能建立发布任务。YouTube 和 X 支持自动发布；TikTok、Facebook、抖音、B站、Kwai、Instagram 当前只生成文案并下载本地成片，不声称自动发布成功。
   发布文案默认调用 `gpt-5.6-terra`，结构化校验失败或短暂网络错误会重试，最终仍不可用时使用来源关键词规则回退。YouTube 只使用标题文案和说明标签；通用文案标签字段保持空白。
9. YouTube worker 回收公开状态和数据，Dashboard 汇总关键词、文案和转化归因。反馈目前生成建议，不会未经审核自动改写生产策略。

## 独立海报库存

海报不参与视频下载、切片或 Remotion 成片链路，但 Dashboard 保留独立库存，用于导入、分类、筛选、人工审核、预览和下载。海报记录与文件继续使用服务器现有存储，不与视频候选状态混用。

## 独立 CTA 库

CTA 记录存放在 `cta_assets`，文件位于 `workspace/server_media/cta/`。只有 Dashboard 人工导入接口可以创建 CTA；发现、下载和源视频导入流程都没有写入 CTA 表的入口。页面只提供导入、预览和删除。

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
