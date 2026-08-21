# Task 5 Web 任务资源归属路由清单

以下 11 个资源入口都调用 `api_v2._owned_job_resource(job_id)`；Web 模式先认证，再检查 live `_jobs._user_id`，否则按 `user_id + job_id` 查询中央仓储。缺少认证统一返回 401，其他用户统一返回 404。

| 资源 | 方法与路由 | Live 任务 | 仅持久化任务 | 跨用户 |
|---|---|---:|---:|---:|
| 状态 | `GET /api/v2/jobs/<job_id>/status` | 支持 | 支持 | 404 |
| 错误详情 | `GET /api/v2/jobs/<job_id>/error-detail` | 支持 | 无详情时 409 | 404 |
| 暂停 | `POST /api/v2/jobs/<job_id>/pause` | 支持 | 409 | 404 |
| 取消 | `POST /api/v2/jobs/<job_id>/cancel` | 支持 | 409 | 404 |
| 实时恢复 | `POST /api/v2/jobs/<job_id>/resume` | 支持 | 409，改走历史恢复 | 404 |
| 结果 | `GET /api/v2/jobs/<job_id>/result` | 支持 | done 结果支持 | 404 |
| 源 PDF | `GET /api/v2/source-pdf/<job_id>` | 支持 | 支持 | 404 |
| 编译日志 | `GET /api/v2/source-pdf/<job_id>/compile-log` | 支持 | 支持 | 404 |
| 节点定位 | `GET /api/v2/source-pdf/<job_id>/locate` | 支持 | 支持 | 404 |
| HTML 导出 | `POST /api/v2/export/<job_id>` | 支持 | done 结果支持 | 404 |
| 工件导出 | `POST /api/v2/export/<job_id>/artifacts` | 支持 | 数据库结果降级导出 | 404 |

补充检查：

- `POST /api/v2/jobs` 和 `POST /api/v2/agent-import` 在解析文件、创建目录或写库前验证 Web 会话。
- `POST /api/v2/history/<hist_id>/resume` 独立使用 `get_owned_history(user_id, hist_id)`，同样不会读取其他用户记录。
- Web 工件导出忽略客户端 fallback nodes/edges，只使用所属 live/persisted 结果。
- Web 源 PDF 与日志路径只能由 `_source_pdf_dir(job_id)` 加安全 basename 重建，并验证解析后的直接父目录。


