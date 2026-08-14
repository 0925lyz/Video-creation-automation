# 验证报告

验证时间：2026-08-14。所有命令均针对本目录中的服务器快照和脱敏数据执行，不会修改生产服务器。

## 已通过

- 归档安全校验：通过。未发现 `.env`、Cookie、私钥、媒体二进制、超过 25 MiB 的单文件或已知凭据模式。
- 脱敏 SQLite：`PRAGMA integrity_check` 返回 `ok`；表关系与 643 条候选记录保留。
- Python 归档工具：`compileall` 通过。
- JSON 清单：全部可解析。
- Shell 拉取脚本：`bash -n` 通过。
- JavaScript/CJS/MJS：`node --check` 通过。
- Remotion TypeScript：使用锁定依赖执行 `tsc --noEmit`，通过。

## 生产代码测试

在 `server-snapshot/` 使用项目虚拟环境运行：

```text
python -m pytest -q
110 passed, 1 failed in 15.45s
```

失败用例：

```text
tests/test_platform_and_brand.py::test_source_outro_trim_is_upstream_of_analysis_and_review_metadata
```

该用例预期尾图裁剪后的 `source_outro_trimmed.mp4` 进入分析阶段，实际没有调用分析器，断言中的 `analyzed_media` 为 `[]`。这是生产快照中现存的源码与测试行为差异；为保证归档忠实，未在本提交中改写服务器代码。

## 依赖安装说明

本机 pnpm 11 在临时安装 Remotion 依赖时默认拦截 `esbuild@0.28.1` 的安装脚本，因此安装命令返回安全策略错误。依赖文件仍成功解析并安装，随后直接调用本地 TypeScript 编译器完成了无输出构建。临时 `node_modules` 未进入 Git。
