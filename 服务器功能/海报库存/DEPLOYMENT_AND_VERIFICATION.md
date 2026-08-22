# 部署与验证

## 部署

- 生产地址：<https://factory.jarg.top/>
- 应用目录：`/opt/jaguartv-content-factory-vnext`
- systemd 服务：`jaguartv-content-factory-vnext`
- 部署时间：2026-08-22
- 部署后服务状态：`active`
- 部署后磁盘：178G 总量，70G 可用，59% 已用

服务器存在定制配置改动，因此未运行会 stash 和重置工作树的全量同步脚本。部署沿用现有应用目录、虚拟环境、数据库迁移和 systemd 服务，通过精确文件更新完成。

## 回滚备份

生产备份保留在：

```text
/opt/jaguartv-content-factory-vnext/workspace/deploy-backups/poster-import-20260822T085309Z
```

包含部署前代码归档和 SQLite 在线备份。本仓库只记录位置，不包含备份内容。

## 自动验证

- 本地全量测试：264 passed
- 服务器海报专项测试：30 passed
- Python 语法：通过
- JavaScript 语法：通过
- Remotion 构建：通过
- `factory.sh doctor`：ready
- `git diff --check`：通过
- 数据库副本迁移：integrity `ok`
- 生产数据库迁移：integrity `ok`

## 浏览器验证

隔离数据库中执行完整流程：

```text
导入图片
-> 自动进入待审核
-> 打开文案设计
-> 添加附图
-> 保存并刷新
-> 再次打开确认持久化
-> 预览主海报和附图
-> 审核通过
-> 确认文案与附图仍完整
```

结果：

- 1440px、768px、375px 均通过。
- 页面无水平溢出，操作按钮无重叠。
- 横图、竖图、方图均未拉伸或裁切。
- 控制台错误：0。
- 失败网络请求：0。
- 本地实验指标：LCP 72ms、CLS 0.067、INP 56ms。
- 生产环境执行只读入口、限制、主图解码和响应式验证，未上传、审核或删除生产数据。

截图位于本目录 `screenshots/`。仓库没有历史视觉基线，因此这些截图用于验收记录，不能作为自动像素差异结论。
