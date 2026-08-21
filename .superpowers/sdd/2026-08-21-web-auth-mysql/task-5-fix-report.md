# Task 5 修复轮次 1 报告

## 状态

`task-5-review.md` 的 4 项问题已全部修复，仅修改 writable mirror，未修改正式源码、未提交 Git。

## TDD 证据

生产代码修改前新增针对性测试并运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py -q -k "settings_repository or async_persistence or resume_persistence or persisted_done_artifact or explicit_desktop"
```

RED：`10 failed, 40 deselected`。失败分别命中：

- 仓储对 4 种非 object config 抛 `AttributeError`；
- done/error/progress 写库失败未形成稳定公开状态；
- resume 写库失败仍返回 202 并启动 worker；
- `_jobs` 清空后数据库 done 记录 artifact export 固定返回 409；
- 显式桌面模式的 SQLite source-PDF 与旧请求体 artifact fallback 均返回 404。

实现后同一组测试 GREEN：`18 passed, 32 deselected in 13.24s`。

## 四项修复

### 1. 统一异步持久化失败策略

- 新增 `_mark_persistence_error()`，只输出稳定的 `persistence_error`、中文标题和用户提示；清除 traceback，不向客户端暴露 SQL、驱动异常或 LLM 密钥。
- 新增 `_persist_job_state()`，done、error、worker 异常退出和 stage/source-PDF progress 统一检查 `_upsert_job_history()` 结果。
- 写库失败时 live job 转为 `status=error`、`result=None`，后续 worker 事件不能重新覆盖为 done。
- job status 显式返回 `persistence_error: true/false`；失败任务的 `error_code` 为 `persistence_error`。
- resume 在启动 worker 前写入 running。失败时恢复修改前的 status/stage/stages/result 和 cache manifest 状态，标记 persistence error，返回稳定 503，绝不调用 `_start_pipeline_attempt()`。
- worker 启动异常对客户端返回稳定消息；其 error 状态写库失败同样进入 persistence-error 分支。

测试覆盖 done、error、progress、resume 四类失败，并验证响应不含 `top-secret`。

### 2. 重启后的数据库 artifact 降级导出

- persisted done resource 不再因缺少 live `source` 字段固定返回 409。
- `_jobs` 清空后，所有者可直接用数据库原生 nodes/edges 生成 degraded ZIP。
- Web 请求体 fallback 始终被忽略；测试注入 `id=injected` 与恶意 filename 后，ZIP 中仍是数据库节点。
- 第二用户对同一记录仍得到 404。

### 3. 仅显式桌面模式恢复旧兼容

- `_owned_job_resource()` 仅在 `_desktop_legacy_auth`（由 `AI4MATH_DESKTOP=1` 且无数据库 URL 派生）分支回查 SQLite history。
- SQLite `source_pdf_json` 仍通过 `_read_source_pdf_meta()` 在受控 `_source_pdf_dir(job_id)` 下重建路径。
- 仅 artifact route 可请求 `allow_desktop_missing=True`，使桌面缺失 job 的旧 nodes/edges body fallback 可达。
- Web 分支未改变：无认证 401、跨用户/缺失 owned resource 404、客户端 fallback 不可用。

### 4. settings config 元素类型校验

- 路由拒绝 configs 中的字符串、数组、null 和 bool，稳定返回 400。
- `LearningRepository.upsert_settings()` 同时做防御性校验，抛稳定 `ValueError`，并保证没有半写入。

## 验证

聚焦 Task 5：

```text
50 passed in 27.63s
```

Task 5 + 直接相关脚本：

```text
59 passed in 31.23s
```

Task 1–5 完整回归：

```text
150 passed in 49.12s
```

静态编译：

```text
python -m py_compile api_v2.py storage/learning_repository.py
  tests/test_learning_storage.py scripts/test_agent_import.py
  scripts/test_paused_history_resume.py
exit 0
```

## 修改文件

- `backend/api_v2.py`
- `backend/storage/learning_repository.py`
- `backend/tests/test_learning_storage.py`
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-5-fix-report.md`

## SHA-256

- `backend/api_v2.py`: `282E69996F34359BCCEDD95C75E30357375C318EABAB5C355C669FD6F166634A`
- `backend/storage/learning_repository.py`: `2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`
- `backend/tests/test_learning_storage.py`: `CBD5713E6093911F837E18D6ADE8A4215985E4399A97698BB3E8ACC6C79AF6FC`

## 仍需关注

- 当数据库在异步处理中不可用时，live job 会稳定报告 persistence error；数据库中的最后一条 durable snapshot 可能仍停留在 running。部署监控应对 `persistence_error` 告警，重启后的 orphan 策略仍属于部署启动任务。
- 桌面兼容测试通过运行时标志明确模拟 `AI4MATH_DESKTOP=1`；Web 测试继续在 SQLAlchemy 模式覆盖 401/404 与 SQLite sentinel。
- 真实阿里云 MySQL、对象存储和多实例共享文件系统仍不属于本修复轮次。


