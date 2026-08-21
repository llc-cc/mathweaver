# Task 5 修复轮次 2 独立复审

## Verdict

**CHANGES REQUIRED**

- 普通绝对 `pdf_path` / `log_path`：已拒绝
- `../` 穿越元数据：已拒绝
- 受控 job 目录内的安全 PDF / log：可正常读取
- 文件符号链接逃逸：当前 `candidate.resolve().parent == root` 可拒绝
- **job 目录自身为 junction / 目录符号链接时仍可逃逸：未拒绝（High）**

## 已确认修复

### 1. 旧 SQLite 元数据不再直通绝对文件路径

`_read_source_pdf_meta()` 对 SQLite 行先调用 `_stored_source_pdf_meta()`，最终经
`sanitize_source_pdf_meta()` 同时按 Windows / POSIX 规则只保留 basename。随后
`_source_pdf_context()` 对 PDF、TeX source 和 compile log 全部调用
`_controlled_source_pdf_file()` 重建路径。

独立路由探针确认：

```text
abs_pdf=404
abs_log=404
traversal_pdf=404
traversal_log=404
safe_pdf=200
safe_log=200
locator_status=200
locator_statement_terms=[]
```

其中 locator 的旧 SQLite `source_path` 指向受控根外、且外部 TeX 含可识别标题；
返回的 `statement_terms=[]`，证明没有读取该外部 TeX。

### 2. Web 鉴权、归属与 artifact body 边界未被放宽

- `_owned_job_resource()` 的 Web 分支仍先要求会话；未登录返回 401。
- live 与持久化任务都按 `_user_id` / repository owner 查询；其他用户返回 404。
- Web persisted-only artifact 导出仍采用数据库结果，客户端 body 仅在显式 desktop
  的 legacy fallback 分支使用。
- source PDF、compile log、locator 都先经过统一 `_owned_job_resource()`，未发现新的
  body 路径或绝对路径参数直通。

## Open — job 目录 junction 可绕过受控根检查

文件：`backend/api_v2.py:3709-3719`

当前逻辑先执行：

```python
root = _source_pdf_dir(job_id).resolve()
candidate = (root / name).resolve()
if candidate.parent != root:
    return None
```

该检查能拒绝“job 目录内某个文件是指向外部的 symlink”，但不能拒绝“job 目录本身
是指向外部目录的 junction / directory symlink”。后一种情况下 `root` 已先解析成外部
目录，`candidate.parent` 也等于这个外部 `root`，检查自然通过。

独立 Windows junction 探针创建：

```text
uploads/source_pdfs/junction-job -> <temp>/external
<temp>/external/source.pdf       # 含唯一 outside 内容
SQLite source_pdf_json           # 仅安全 pdf_name=source.pdf
```

实际结果：

```text
MKLINK 0
JUNCTION_ROUTE_STATUS 200
SERVED_OUTSIDE True
```

这不仅影响 source-PDF；同一 junction 下的 `compile.log` 和 `source.tex` 也会分别进入
compile-log 与 locator 的文件读取路径。因此尚不满足“符号链接逃逸 404”和“只允许
`_source_pdf_dir(job_id)` 的真实直接子文件”要求。

建议在解析 job root 后，同时验证它确实是 `_SOURCE_PDF_ROOT.resolve()` 下与当前安全
job 段对应的真实直接子目录；不能只比较 candidate 与已经解析到外部的 root。应增加
目录 junction / directory symlink 的负向回归，至少覆盖 PDF，并最好同时覆盖 log 或
locator。还应顺手拒绝解析结果为 `.` / `..` 等非直接 job 子目录的 job id。

## 独立验证

在 mirror 的 `backend` 目录亲自运行：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py::test_explicit_desktop_mode_restores_sqlite_source_pdf_and_artifact_fallback -q
1 passed in 2.53s

.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py -q
50 passed in 21.77s

.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
59 passed in 24.42s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
150 passed in 46.64s

.\.venv\Scripts\python.exe -m py_compile api_v2.py storage\learning_repository.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
exit 0
```

测试套件全绿，但现有测试只覆盖“外部绝对文件路径 + 内部安全 basename”，没有覆盖
job 根目录自身被 reparse 到外部的场景，因此不能据此批准本轮。

复核 SHA-256 与修复报告一致：

- `backend/api_v2.py`: `63A63F914B1BEB350A26B2AD56A3A853B8E3A3FE88BB7E8BE87DE258C33FE55E`
- `backend/storage/learning_repository.py`: `2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`
- `backend/tests/test_learning_storage.py`: `2A7E555F0614BD8F988E5FBE14CA9427B1E16286AC8C6B7A6E6D25E296C03D68`

## 最终结论

修复轮次 2 已关闭原来的绝对路径直通，但目录 junction 仍能把受控 job 根整体重定向
到外部并读取真实文件。修复 job root containment、增加负向回归并 fresh re-review 前，
Task 5 不应同步到正式源码。

