# Task 5 独立审查报告 — MySQL 学习数据与任务归属

## Verdict

**CHANGES REQUIRED**

开放问题：**4 项（High 2，Medium 2）**。其余已检查的 SQLAlchemy 用户隔离、所有权 401/404、受控 PDF 路径、Web 禁用 SQLite、匿名创建无副作用、仓储 JSON/ISO 边界及现有桌面脚本回归未发现新的阻塞问题。

## Findings

### High 1 — 异步终态与恢复路径忽略持久化失败，会把未落库任务伪装成成功/运行中

- 文件：`backend/api_v2.py:2918`、`backend/api_v2.py:2934`、`backend/api_v2.py:2937`、`backend/api_v2.py:2983`、`backend/api_v2.py:3577`、`backend/api_v2.py:3595`
- `done`、`error`、worker 异常退出以及 resume 的 `running/error` 写入都没有检查 `_upsert_job_history()` 的返回值。
- `done` 事件先把内存状态改为 `done`，即使数据库写入返回 `False`，仍向后续状态/结果查询表现为完成；`_history_persisted` 仍保留 `True`，数据库实际可能继续停留在 `running`。
- 中间进度失败虽然写入 `_persistence_error=True`，但 `job_status()` 会过滤所有下划线字段，注释所称“供状态接口明确暴露”并未实现。
- resume 同样会在数据库未成功更新为 `running` 时启动 worker。

独立复现（将 `_upsert_job_history` 固定返回 `False` 后投递 `done` 事件）：

```text
terminal= True status= done history_persisted= True persistence_error= None
```

这违反“持久化失败不能伪标成功”和完整生命周期持久化要求。需要定义统一失败策略：终态不能在持久化失败时对外报告成功；resume 必须在成功写入 `running` 后再启动；异步失败必须通过状态接口稳定暴露或把任务转为明确错误态，并增加 done/error/resume/progress 四类失败测试。

### High 2 — 清空 `_jobs` 后，已完成任务无法从数据库 JSON 降级导出 artifact ZIP

- 文件：`backend/api_v2.py:1256`、`backend/api_v2.py:3765`
- `_history_job_resource()` 没有恢复 `source` 字段；持久化记录经统一 helper 返回后，`export_artifacts()` 的 `job.get("source") != "pipeline"` 恒为真并返回 409。
- 因此数据库中的 `nodes/edges` 虽然完整，重启或清空内存后仍无法按任务约定生成降级 ZIP。

独立复现：插入 owned `done` `JobSnapshot`、清空 `_jobs`，以所有者调用 artifact export：

```text
409 {'error': 'Complete processing cache is unavailable for this job'}
```

这违反“Persisted done results may be exported from database JSON”。需要让 owned persisted done 直接使用仓储返回的 native `nodes/edges` 降级导出，同时继续忽略客户端 fallback，并增加所有者重启后导出测试。

### Medium 1 — 统一 helper 破坏桌面 SQLite 历史 source-PDF 回退，并使旧 artifact fallback 成为死代码

- 文件：`backend/api_v2.py:1289`、`backend/api_v2.py:3649`、`backend/api_v2.py:3738`
- 桌面分支的 `_owned_job_resource()` 只查内存 `_jobs`，找不到立即返回 404；旧实现的 source-PDF 路径会在内存缺失时从 SQLite `history` 读取 `source_pdf_json`，现在已无法到达。
- `export_artifacts()` 在 helper 返回后才保留 `if not job` 的客户端 fallback，但 helper 对桌面缺失任务已提前返回 404，因此该兼容分支也不可达。

独立复现：桌面模式在 SQLite 写入带安全 `pdf_name` 的 done 历史、创建受控 PDF 文件且不放入 `_jobs`：

```text
GET /api/v2/source-pdf/desktop-persisted
404 {'error': 'Not found'}
```

独立复现旧桌面 artifact fallback：

```text
POST /api/v2/export/missing/artifacts  (body 含 nodes/edges)
404 {'error': 'Not found'}
```

需要在不放宽 Web 所有权规则的前提下保留桌面旧行为，并增加明确的 `AI4MATH_DESKTOP=1` 回归测试。现有相关脚本都通过环境变量进入 Web/SQLAlchemy 模式，无法捕获此回归。

### Medium 2 — settings 接受非对象 config 元素，随后抛异常返回 500

- 文件：`backend/api_v2.py:690`、`backend/storage/learning_repository.py:117`、`backend/storage/learning_repository.py:123`
- 路由只校验 `configs` 是 list，没有校验每项是 JSON object。合法 JSON `{"configs":["bad"],"active_index":0}` 会进入仓储，对字符串调用 `.get()`。

独立复现：

```text
AttributeError: 'str' object has no attribute 'get'
PUT /api/v2/settings -> 500
```

这违反 malformed settings 必须返回 400 的输入边界。应在路由层拒绝任何非对象元素，并补充字符串、数组、null、布尔值元素的参数化测试；仓储边界也可保持防御性校验，但不能以 500 暴露类型错误。

## 已核查通过的关键项

- Web settings/history/proof 的实际分支使用 `LearningRepository`，显式桌面分支才保留 `_get_db`/SQLite。
- `JobSnapshot` 为 frozen dataclass；仓储 client-facing 方法都显式接收 `user_id`，返回深拷贝后的 dict/list 和 ISO 时间，不泄漏 ORM 实体。
- `upsert_job_progress` 在已有 job ID 属于其他用户时不更新记录；主键冲突由事务回滚并返回 `False`。
- source-PDF 持久化仅保留受控元数据和 basename；Web 下载重新锚定 `_source_pdf_dir(job_id)`，解析路径必须是直接子项，符号链接/穿越不能逃逸。
- 11 个任务资源入口均调用 `_owned_job_resource()`；Web 未登录为 401，跨用户为 404。
- Web create/import 在读取上传内容、建目录或写任务前鉴权；匿名 create 的现有测试验证无 `_jobs`、目录及历史行副作用。
- Web artifact 路由不会在 owned resource 缺失时接受客户端 nodes/edges；当前问题是数据库 owned resource 被错误拒绝，而不是安全回退被绕过。
- settings 的 JSON object、configs list、非 bool integer active_index 和索引范围基础校验有效；proof workspace 以复合主键隔离用户。

## 测试与验证证据

亲自运行：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py -q
36 passed in 46.76s

.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
45 passed in 52.67s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
136 passed in 98.69s

.\.venv\Scripts\python.exe -m py_compile api_v2.py storage\learning_repository.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
exit 0
```

额外独立探针复现了上述四项开放问题。现有全绿回归不能覆盖这些缺口，不能据此批准 Task 5。

复核 SHA-256：

- `backend/storage/learning_repository.py`: `9F143277835B570F8597409F91AE63902015B7E3F3EA5EE034861ED4D52747C6`
- `backend/api_v2.py`: `D17453B8427187AAD20C159521D485C7D2C716F86F7FD67874AC0204F9EF1B3F`

## 最终结论

Task 5 的主体分层和 Web 所有权保护方向正确，但四项确认问题中有两项直接破坏核心持久化契约。修复并补充相应失败/重启/桌面回归测试后，需要进行一次全新的 scoped re-review。

