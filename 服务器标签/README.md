# 服务器标签功能包

这个目录是 JaguarTV 服务器“标签分类 / 增长分析 / 当日分类关键词 / 内容库存分类筛选”的完整打包快照。

## 包含内容

- `data/category_labels.json`：18 个榜单标签的固定顺序、是否需要关键词、分类提示词。
- `scripts/import_daily_keywords.py`：把“标签：关键词1、关键词2；关键词3”格式的每日关键词导入服务器 `workspace/factory.db`。
- `scripts/clear_tag_keywords.py`：每周清空 `daily_keywords:*` 标签关键词库，并自动备份数据库。
- `scripts/carry_forward_tag_keywords.py`：周二到周日如果当天没有标签关键词，自动沿用最近一次标签关键词。
- `server-snapshot-manifest.json`：本次包内文件清单、SHA256、排除项说明。
- `server_snapshot/`：可迁移的服务器功能源码快照，包含后端、前端、脚本、测试、示例配置、品牌资产和项目本地 agent skill。

已排除内容：`.env`、真实 token、私钥、`workspace/factory.db`、下载/制作出来的视频、平台会话 cookie、OAuth 刷新令牌、`.venv`、`node_modules`、缓存文件，以及本机真实 `config/pipeline.yaml`。

## 服务器部署

在服务器项目目录 `/opt/jaguartv-content-factory-vnext` 中同步 `server_snapshot/` 内对应文件后执行：

```bash
.venv/bin/python -m py_compile src/jaguartv_factory/dashboard.py
node --check src/jaguartv_factory/web/app.js
sudo systemctl restart jaguartv-content-factory-vnext
```

验证：

```bash
curl -sS 'http://127.0.0.1:8788/api/category-keywords?date=today'
```

返回的 `rows` 应该包含 18 行标签。

## 本地验证

在仓库根目录运行：

```bash
python3 -m json.tool 服务器标签/data/category_labels.json >/dev/null
PYTHONPATH=服务器标签/server_snapshot/src python3 -m py_compile 服务器标签/server_snapshot/src/jaguartv_factory/dashboard.py
node --check 服务器标签/server_snapshot/src/jaguartv_factory/web/app.js
```

## 每日关键词导入

将当天关键词保存为文本，例如 `/tmp/daily_keywords.txt`，格式：

```text
ai短剧：巴西 AI短剧 葡语、série curta IA Brasil、AI short drama Brazil
教程及优点展示类：无
官方性质类：无
```

在服务器项目根目录运行：

```bash
.venv/bin/python 服务器标签/scripts/import_daily_keywords.py /tmp/daily_keywords.txt --db workspace/factory.db --date today
```

导入脚本会跳过 `无`、`暂无`、`none` 等空值，并把来源写成 `daily_keywords:<标签>`，用于前端稳定归类。

## 每周一自动清空

服务器使用 systemd timer：

```bash
sudo systemctl status jaguartv-clear-tag-keywords.timer
sudo systemctl list-timers --all jaguartv-clear-tag-keywords.timer
```

当前服务器时区是 `Asia/Shanghai`，定时器设置为每周一 `11:05 CST`，等价于巴西圣保罗时间周一 `00:05`。清空范围只包含：

```sql
source LIKE 'daily_keywords:%'
```

不会删除 Google Trends 或其它来源的热词记录。

## 周内自动沿用

服务器另有 systemd timer：

```bash
sudo systemctl status jaguartv-carry-forward-tag-keywords.timer
sudo systemctl list-timers --all jaguartv-carry-forward-tag-keywords.timer
```

当前服务器时区是 `Asia/Shanghai`，定时器设置为周二到周日 `11:10 CST`，等价于巴西圣保罗时间 `00:10`。如果当天已经有 `daily_keywords:*`，脚本不会重复插入；如果当天没有，会复制最近一天的标签关键词到当天。

## 当前标签顺序

1. ai短剧
2. 明星名人歌手
3. 足球球星
4. 足球类
5. 新闻类
6. 音乐类
7. 肥皂剧（电视剧、电影）
8. 少儿剧
9. 成人频道
10. 纪录片（美食、动物、地区发展）
11. 综艺
12. 社交挑战
13. 舞蹈
14. 教程及优点展示类
15. 官方性质类
16. 合作类
17. 运营教学类
18. 教程及答疑类
