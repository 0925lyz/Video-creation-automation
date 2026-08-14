# 脱敏生产数据快照

`production-snapshot/` 是 2026-08-14 导出的服务器生产状态样本，用于离线验证数据库兼容性、候选状态统计和存储规模。它包含 643 条匿名候选记录，但不包含视频、Cookie、会话、令牌、真实 URL、真实路径、原标题、人员信息、自由文本元数据或错误日志正文。

为遵守本次仓库范围，热词和趋势运行表在提交前已从 SQLite 和 `schema.sql` 物理删除。`snapshot-summary.json` 记录脱敏规则、表计数和数据库 SHA-256；`media-inventory.csv` 只保存不可逆路径标识、类别、扩展名、大小和修改时间。

验证快照：

```bash
sqlite3 data/production-snapshot/factory.sanitized.db 'PRAGMA integrity_check;'
shasum -a 256 data/production-snapshot/factory.sanitized.db
```

重新从服务器数据库导出时使用：

```bash
python scripts/export-sanitized-snapshot.py \
  --database /path/to/factory.db \
  --output-dir /safe/output/directory \
  --media-list /path/to/anonymized-input-list.tsv
```

导出器不会修改源数据库，输出目录不得指向生产 `workspace/`。提交新快照前必须再次执行凭据扫描和 SQLite 完整性检查。
