# 服务器标签功能包

这个目录是 JaguarTV 服务器“标签分类 / 增长分析 / 当日分类关键词 / 内容库存分类筛选”的完整打包快照。

## 包含内容

- `data/category_labels.json`：18 个榜单标签的固定顺序、是否需要关键词、分类提示词。
- `scripts/import_daily_keywords.py`：把“标签：关键词1、关键词2；关键词3”格式的每日关键词导入服务器 `workspace/factory.db`。
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
