# 数据库与存储

## 数据库

继续使用共享 `workspace/factory.db`，没有创建独立数据库。迁移由 `connect_db()` 幂等执行，兼容已有不完整表。

`posters` 在原结构上增加：

- `original_name`
- `mime_type`
- `width`
- `height`
- `size_bytes`
- `sha256`
- `content_title`
- `content_copy`
- `content_tags_json`

新增 `poster_attachments` 表，共 15 个字段，包含附图 ID、海报 ID、受控文件标识、原文件名、MIME、尺寸、大小、SHA-256、排序、操作者、时间及软删除时间。

审核、导入、文案保存、附图增删替换排序和清理失败继续写入 `poster_audit_events`。

## 文件布局

根目录由 `storage.root` 与 `storage.poster_subdir` 组合，不硬编码服务器路径：

```text
posters/
  original/                 原始海报
  thumbnails/               WebP 派生缩略图
  attachments/{poster_id}/  海报专属附图
  .pending/                 未完成上传
  .trash/                   事务删除隔离
```

原图始终保留，边框和 `contain` 仅用于界面展示。缩略图作为派生文件单独保存。

## 一致性与清理

- 上传：临时写入、解码验证、原子移动、数据库事务；数据库失败删除原图和缩略图。
- 替换：先写入新文件，将旧文件移入隔离区；事务失败恢复旧文件并删除新文件。
- 删除附图：先隔离文件，再软删除关系；事务失败恢复文件。
- 删除主海报：主记录和附图关系软删除，专属文件隔离后清理。
- 共享文件检查：仍被其他活动海报或附图引用的文件不会删除。
- 隔离清理失败：数据库状态保持可恢复，并记录审计事件。

## 生产迁移结果

- 迁移前活动海报：30
- 迁移后活动海报：30
- `posters`：25 列
- `poster_attachments`：15 列
- `PRAGMA integrity_check`：`ok`
