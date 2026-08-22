# 海报库存 API

接口沿用 Dashboard 的 `/api/posters` 风格和现有管理员认证边界。

## 查询与媒体

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| GET | `/api/posters` | 状态、分类和分页组合查询 |
| GET | `/api/posters/counts` | 各状态实时数量 |
| GET | `/api/posters/{id}` | 海报、文案和附图详情 |
| GET | `/api/posters/{id}/preview` | 受控原图预览 |
| GET | `/api/posters/{id}/thumbnail` | 受控缩略图 |
| GET | `/api/posters/{id}/download` | 仅审核通过后下载 |
| GET | `/api/posters/import/limits` | 前端上传限制和格式 |
| GET | `/api/posters/{id}/attachments/{attachment_id}/preview` | 受控附图预览 |

## 写操作

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| POST | `/api/posters/import` | 原始图片流式导入，固定进入待审核 |
| POST | `/api/posters/{id}/approve` | 根据数据库真实状态推进一阶段 |
| POST | `/api/posters/{id}/delete` | 软删除记录并安全清理专属文件 |
| POST | `/api/posters/{id}/content` | 保存标题、文案和标签 |
| POST | `/api/posters/{id}/attachments` | 上传专属附图 |
| POST | `/api/posters/{id}/attachments/reorder` | 原子保存完整附图顺序 |
| POST | `/api/posters/{id}/attachments/{attachment_id}/replace` | 原子替换附图 |
| POST | `/api/posters/{id}/attachments/{attachment_id}/delete` | 删除附图关系和专属文件 |

## 关键约束

- 客户端不能指定导入后的状态或审核目标状态。
- `PENDING_SCREENING` 只能进入 `PENDING_REVIEW`。
- `PENDING_REVIEW` 只能进入 `APPROVED`。
- 重复通过、状态不匹配和已删除记录返回冲突错误。
- 下载接口重新检查 `APPROVED`，不能依赖按钮隐藏。
- 写入使用参数化 SQL、事务和受控 ID。
- 未知异常仅记录到服务器日志，客户端返回统一 500 文案。
