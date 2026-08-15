# 验证报告

验证时间：2026-08-15。所有命令均针对本目录中的服务器快照和脱敏数据执行，不会修改生产服务器。

## 已通过

- 归档安全校验：通过。未发现 `.env`、Cookie、私钥、媒体二进制、超过 25 MiB 的单文件或已知凭据模式。
- 脱敏 SQLite：`PRAGMA integrity_check` 返回 `ok`；表关系与 643 条候选记录保留。
- Python 归档工具：`compileall` 通过。
- JSON 清单：全部可解析。
- Shell 拉取脚本：`bash -n` 通过。
- JavaScript/CJS/MJS：`node --check` 通过。
- 服务器快照 Python 编译：`compileall -q src 服务器功能/server-snapshot/src 服务器优化完整提交/server-snapshot/src` 通过。
- 前端脚本：`node --check src/jaguartv_factory/web/app.js` 和 `node --check src/jaguartv_factory/web/copywriter.js` 通过。
- Git 空白检查：`git diff --check` 通过。

## 生产代码测试

在根项目使用项目虚拟环境运行：

```text
python -m pytest tests/test_core.py tests/test_dashboard.py tests/test_platform_and_brand.py -q
104 passed, 1 skipped in 2.28s
```

重点覆盖：文案设计归档、公开上传无需服务器令牌、公开 dashboard 默认可访问、库存多版本输出、下载/制作/审核核心流程。

## 依赖安装说明

本机 pnpm 11 在临时安装 Remotion 依赖时默认拦截 `esbuild@0.28.1` 的安装脚本，因此安装命令返回安全策略错误。依赖文件仍成功解析并安装，随后直接调用本地 TypeScript 编译器完成了无输出构建。临时 `node_modules` 未进入 Git。
