# Task 5 修复轮次 1 独立复审

## Verdict

**CHANGES REQUIRED**

- Addressed：**3 项**
- Open：**1 项（Medium）**

原审查的异步持久化失败、重启后数据库降级导出、settings 非对象校验已经修复。显式桌面 SQLite 回退本身也恢复了，但旧记录中的绝对 `pdf_path` / `log_path` 仍可绕过受控任务目录，因此第 3 项只能判定为部分完成。

## 原 4 项逐项复核

### Addressed 1 — 异步终态、进度、worker 异常和 resume 不再伪成功

- `backend/api_v2.py:2911-2930` 统一将写库失败转为稳定 `persistence_error`；会清空结果和 traceback，不返回数据库、SQL 或运行密钥。
- `backend/api_v2.py:2933-2996` 在 `_jobs_lock` 内串行处理事件；done、error 和阶段进度都检查持久化结果。进入 `_persistence_error` 后，后续 worker 事件不能再覆盖为 done。
- `backend/api_v2.py:3021-3039` 的 worker 异常退出同样调用统一持久化失败路径。
- `backend/api_v2.py:3590-3660` 的 resume 在启动 worker 前持久化 running；失败时恢复深拷贝快照及 cache manifest、标记稳定错误并返回 503，未调用 `_start_pipeline_attempt()`。成功重试才清除 `_persistence_error`。
- `backend/api_v2.py:3453-3474` 明确公开布尔 `persistence_error` 和稳定错误码；内部下划线字段、原始 error/detail 及 LLM 配置不会进入 status 响应。

现有参数化测试覆盖 done/error/progress，resume 测试覆盖快照恢复及 worker 未启动。终态和后续事件覆盖路径的锁与 guard 未发现可复现缺口。

### Addressed 2 — 重启后 owned persisted done 可从数据库 JSON 降级导出

- `_history_job_resource()` 恢复仓储返回的原生 `nodes` / `edges`。
- `export_artifacts()` 对 persisted-only done 任务使用数据库结果；请求体中的注入 nodes、filename 不参与 Web 降级输出。
- 所有权仍由 `_owned_job_resource()` 在读取资源前完成；其他用户得到 404。
- 回归测试实际解压 ZIP，确认节点为数据库中的 `[{"id": 1, "title": "定理"}]`，而非请求体注入值。

### Open 1 — 显式桌面 SQLite source-PDF 回退仍可读取受控目录之外的绝对路径

- 文件：`backend/api_v2.py:358-400`、`backend/api_v2.py:3694-3706`
- `_legacy_history_job_resource()` 会读取旧 SQLite 的 `source_pdf_json`；当旧记录带 `pdf_path` / `log_path` 时，`_read_source_pdf_meta()` 保留这些绝对路径。
- `_controlled_source_pdf_file()` 在 `_desktop_legacy_auth` 下遇到 `path_key` 会直接 `Path(...)` 返回，跳过随后“必须是 `_source_pdf_dir(job_id)` 直接子项”的校验。
- 因此显式 desktop 的无鉴权资源路由可发送任意现存绝对路径文件。这不放宽 Web，但不满足本轮要求的路径安全，也违反 Task 5 的“只用存储 basename 在受控目录重建”规则。

独立探针将 SQLite 历史的 `pdf_path` 指向受控目录外的临时 PDF，未在 `_source_pdf_dir(job_id)` 创建任何文件：

```text
status 200
served_outside True
controlled_exists False
```

应删除 desktop 的绝对路径直通分支。对 SQLite 元数据也只取安全 basename，并在 `_source_pdf_dir(job_id).resolve()` 下重建，解析后确认 `candidate.parent == root` 且拒绝符号链接逃逸。补充旧 SQLite `pdf_path`、`log_path` 指向目录外时返回 404 的回归；现有 desktop 测试只覆盖安全 `pdf_name`，未覆盖该分支。

### Addressed 3 — settings 非 object 元素稳定返回 400，仓储无半写

- 路由在进入仓储前拒绝 string、array、null、boolean config 元素。
- `LearningRepository.upsert_settings()` 在打开事务前做同样的防御性类型检查并抛稳定 `ValueError`。
- 参数化测试覆盖 4 种元素类型，并确认仓储拒绝后仍是空设置，未发生半写。

## 独立验证

在 `backend` 目录亲自运行：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py -q
50 passed in 30.37s

.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
59 passed in 24.11s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
150 passed in 44.81s

.\.venv\Scripts\python.exe -m py_compile api_v2.py storage\learning_repository.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
exit 0
```

复核 SHA-256 与修复报告一致：

- `backend/api_v2.py`: `282E69996F34359BCCEDD95C75E30357375C318EABAB5C355C669FD6F166634A`
- `backend/storage/learning_repository.py`: `2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`
- `backend/tests/test_learning_storage.py`: `CBD5713E6093911F837E18D6ADE8A4215985E4399A97698BB3E8ACC6C79AF6FC`

## 最终结论

修复轮次已解决 3 项，仍有 1 项已实际复现的桌面任意路径读取问题。修复绝对路径直通并增加负向路径测试后，再做一次 scoped re-review；在此之前 Task 5 不应同步到正式源码。

