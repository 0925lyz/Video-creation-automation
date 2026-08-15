# 服务器标签功能包

这个目录是 JaguarTV 服务器“增长分析 / 当日分类关键词 / 内容库存分类筛选”的完整打包快照。

## 包含内容

- `data/category_labels.json`：18 个榜单标签的固定顺序、是否需要关键词、分类提示词。
- `scripts/import_daily_keywords.py`：把“标签：关键词1、关键词2；关键词3”格式的每日关键词导入服务器 `workspace/factory.db`。
- `server_snapshot/src/jaguartv_factory/dashboard.py`：后端接口与标签分类规则快照。
- `server_snapshot/src/jaguartv_factory/web/app.js`：前端状态拉取、分类筛选、当日分类关键词表格渲染快照。
- `server_snapshot/src/jaguartv_factory/web/index.html`：增长分析页面表格结构快照。
- `server_snapshot/src/jaguartv_factory/web/styles.css`：分类关键词表格样式快照。
- `server_snapshot/tests/test_dashboard.py`：对应的回归测试快照。

## 服务器部署

在服务器项目目录 `/opt/jaguartv-content-factory-vnext` 中替换对应文件后执行：

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
