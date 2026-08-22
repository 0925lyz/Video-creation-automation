# 海报库存交付包

本目录归档 JaguarTV Content Factory 的“海报库存”和“导入海报”完整交付物。生产代码仍位于仓库根目录的原始模块中，本目录用于审计、迁移、对比和灾难恢复，不是独立运行项目。

归档时间：2026-08-22

生产地址：<https://factory.jarg.top/>

## 包含内容

- `source/`：本任务涉及的完整生产源码和测试副本，保持仓库相对路径。
- `changes/poster-inventory-and-import.patch.gz`：相对于任务开始版本的无损压缩、可重放 Git 补丁。
- `screenshots/`：海报库存、导入、预览、文案设计在本地及生产环境的桌面、平板和移动端截图。
- `TASK_REPORT.md`：功能、安全、测试和需求完成状态。
- `API.md`：海报接口及权限规则。
- `DATABASE_AND_STORAGE.md`：兼容迁移、文件布局和清理策略。
- `DEPLOYMENT_AND_VERIFICATION.md`：部署、备份、测试和浏览器验证记录。
- `manifest.json`：机器可读交付清单。

## 生产文件

以下根目录文件是可运行实现，归档副本应与其保持一致：

- `src/jaguartv_factory/core.py`
- `src/jaguartv_factory/dashboard.py`
- `src/jaguartv_factory/posters.py`
- `src/jaguartv_factory/web/app.js`
- `src/jaguartv_factory/web/index.html`
- `src/jaguartv_factory/web/styles.css`
- `tests/test_posters.py`

## 使用方式

检查补丁是否可应用：

```bash
gzip -dc 服务器功能/海报库存/changes/poster-inventory-and-import.patch.gz \
  | git apply --check
```

需要从归档恢复单个文件时，应先对比根目录当前版本，避免覆盖后续功能：

```bash
git diff --no-index \
  src/jaguartv_factory/posters.py \
  服务器功能/海报库存/source/src/jaguartv_factory/posters.py
```

## 安全排除

本包不包含生产 `workspace/factory.db`、媒体文件、数据库备份、`.env`、Token、Cookie、SSH 私钥、平台会话或服务器日志。数据库迁移和备份位置仅在文档中记录。
