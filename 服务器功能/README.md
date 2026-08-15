# 服务器功能

这个目录保存的是当前服务器 `/opt/jaguartv-content-factory-vnext` 的功能快照，方便把服务器上已经调好的功能留在仓库里对比、迁移和继续开发。

本次补充时间：2026-08-15。快照已合并 `服务器优化完整提交` 中保留的部署/归档思路，并补入当前线上可用的服务器库存、发现素材、文案设计、公开上传、归档和待审核流程代码。

## 目录

- `server-snapshot/`: 服务器当前代码、配置、网页、脚本、测试、品牌素材和 Remotion 模板源码。
- `server-snapshot/src/jaguartv_factory/dashboard.py`: 服务器库存界面、任务接口、文案设计接口、上传接口、下载/制作按钮接口。
- `server-snapshot/src/jaguartv_factory/web/index.html`: 服务器内容库存主界面。
- `server-snapshot/src/jaguartv_factory/web/app.js`: 前端交互、发现素材、服务器成片、文案设计弹窗、Logo/图片上传、审核按钮逻辑。
- `server-snapshot/src/jaguartv_factory/web/styles.css`: 库存界面和文案设计界面样式。
- `server-snapshot/src/jaguartv_factory/core.py`: 发现、去重、下载、制作、待审核包、Remotion 调用主流程。
- `server-snapshot/src/jaguartv_factory/sources.py`: YouTube、TikTok、Facebook、Douyin 等素材源适配器。
- `server-snapshot/src/jaguartv_factory/browser_scraper.py`: 浏览器兜底爬取和解析。
- `server-snapshot/src/jaguartv_factory/server_store.py`: 服务器素材上传、库存、公开媒体路径、审核包归档。
- `server-snapshot/src/jaguartv_factory/remotion_template/`: 服务器成片和文案设计版视频的 Remotion 模板源码。
- `server-snapshot/config/pipeline.yaml`: 服务器当前制作配置。
- `server-snapshot/config/keywords.brazil.yaml`: 当前巴西关键词配置。
- `server-snapshot/scripts/`: 服务器安装、同步、爬取、制作辅助脚本。
- `server-snapshot/tests/`: 当前服务器功能对应测试。

## 本次补充重点

- 文案设计版生成后会正确归档到服务器公开媒体目录，避免生成成功但 `/media/review/...mp4` 404。
- 库存列表会区分 `成片 · 通用版/FB版` 和 `文案设计版 · 通用版/FB版`。
- Logo/贴图等上传入口不再需要服务器令牌。
- 服务器库存、上传、回调和 dashboard 管理接口保留各自的安全边界；生产密钥仍只从环境变量读取。
- 发现素材、下载、制作、待审核、下载登记、Copywriter 页面和 Remotion 模板源码统一放入快照。

## 已排除

为了避免把运行数据和敏感内容放进仓库，这次没有导入：

- `.env`
- `.venv/`
- `node_modules/`
- `workspace/`
- 数据库：`factory.db`、`*.sqlite`
- 平台登录状态：`cookies.txt`
- 私钥/证书：`*.pem`、`*.key`
- 已下载素材和成片：`*.mp4`、`*.mov`、`*.mkv`、`*.webm`、`*.wav`、`*.mp3`
- Python/测试缓存和 macOS 资源叉文件

## 使用说明

这个目录是服务器功能快照，不是独立运行目录。要把某个服务器功能合并回主项目时，优先对比 `server-snapshot/` 与仓库根目录同名文件，再按模块迁移。

常用入口：

```bash
./server-snapshot/.agents/skills/jaguartv-content-factory/scripts/factory.sh doctor
./server-snapshot/.agents/skills/jaguartv-content-factory/scripts/factory.sh discover --platform youtube --limit 3
./server-snapshot/.agents/skills/jaguartv-content-factory/scripts/factory.sh download --candidate <id>
./server-snapshot/.agents/skills/jaguartv-content-factory/scripts/factory.sh produce --candidate <id>
./server-snapshot/.agents/skills/jaguartv-content-factory/scripts/factory.sh review
```
