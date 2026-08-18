# 生产部署快照

这些文件来自 2026-08-14 的服务器配置并已移除敏感值。证书私钥、`.env` 和平台登录态不在仓库中。

## 服务关系

- `jaguartv-content-factory.service`：旧项目，监听 `0.0.0.0:8787`。
- `jaguartv-content-factory-vnext.service`：vNEXT 项目，监听 `127.0.0.1:8788`。
- Nginx 当前反代 `127.0.0.1:8787`，与历史上切换到 vNEXT 8788 的状态不同。
- `jaguartv-douyin-source.service`：抖音本地解析服务，端口 8000。
- `jaguartv-xhs-source.service`：小红书本地解析服务，端口 5556。
- `jaguartv-google-trends.timer`：圣保罗时间每日 08:00 同步关键词。
- `jaguartv-clean-tmp.timer`：每小时清理临时渲染缓存。

部署前应先统一 Nginx 与目标 systemd 服务端口，再执行 `nginx -t` 和服务健康检查。
