# 外部能力整合

服务器功能分为三层：

1. 主仓库适配代码：`server-snapshot/src/jaguartv_factory/`，这是实际接入生产流程的代码。
2. 外部 GitHub 源码：`external-repositories.lock.json` 锁定提交，`fetch-external-repositories.sh` 可按需检出到 `third_party/`。
3. Codex 操作 Skills：`operator-skills.lock.json`，记录用来下载、诊断和验证的 Skill 版本；运行时不依赖 Codex 本地目录。

## 对应关系

| 外部能力 | 仓库接入位置 | 用途 |
|---|---|---|
| MediaCrawler | `mediacrawler.py`, `browser_scraper.py` | 中文平台发现和登录态浏览器兜底 |
| Scrapling | `browser_scraper.py`, `sources.py` | TikTok/Facebook/小红书页面抓取兜底 |
| pytrends | `trends.py` | 巴西每日 Google Trends 关键词 |
| PaddleOCR | `ai_services/paddle_ocr_engine.py`, `workbuddy_adapter.py` | 动态中文字幕检测，失败时回退 Tesseract |
| pyVideoTrans | `pyvideotrans_adapter.py` | 可选中文到 pt-BR 本地化适配 |
| Remotion | `remotion_template/` | 通用版、FB版、文案设计和移动画布渲染 |
| mattpocock/skills | 开发规范参考 | Skill 编排与小步验证风格，不是生产运行依赖 |

外部仓库保持各自许可证和版权。主仓库不会把它们的 `.git` 历史或模型权重复制进来。执行以下命令可重建全部上游源码：

```bash
bash 服务器优化完整提交/integrations/fetch-external-repositories.sh
```
