# Task 5 修复轮次 2 报告

## 状态

`task-5-fix-review.md` 的唯一开放问题已修复。仅修改 writable mirror，未修改正式源码、未提交 Git。

## TDD 证据

生产代码修改前扩展显式桌面回归测试，创建真实的受控目录外 PDF 与 compile log，并运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py::test_explicit_desktop_mode_restores_sqlite_source_pdf_and_artifact_fallback -q
```

RED：`1 failed`。外部绝对 `pdf_path` 被路由读取并返回 200，证明测试捕获了复审漏洞。

移除绝对路径直通并统一重新锚定后，同一测试 GREEN：`1 passed in 2.31s`。

## 修复内容

- 删除 `_controlled_source_pdf_file()` 中 desktop 绝对 `path_key` 直接 `Path(...)` 返回的例外。
- 旧 SQLite `source_pdf_json` 读取后先经过 `_stored_source_pdf_meta()`：只保留公开状态、URL、错误和 `pdf_name/source_name/log_name` 安全 basename，不保留数据库中的绝对路径。
- `_source_pdf_context()` 对 PDF、TeX source 和 compile log 三类路径统一调用 `_controlled_source_pdf_file()`，重新锚定到 `_source_pdf_dir(job_id)`。
- 候选路径调用 `resolve()` 后必须满足 `candidate.parent == controlled_root`；指向目录外的 symlink 会因解析后父目录变化而被拒绝。
- PDF 和 log 路由还要求候选是受控目录内的普通文件；目录外绝对路径、穿越后不存在的受控 basename 均返回 404。
- locator 也只接收已重新锚定的 `pdf_path/source_path`，避免旧 SQLite `source_path` 被用于读取目录外 TeX 或调用 SyncTeX。
- 显式桌面模式仍保留安全 basename 回退和旧 artifact body fallback；Web 401/404 与所有权分支未修改。

## 测试覆盖

显式桌面回归同时验证：

- SQLite 绝对 `pdf_path` 指向真实外部文件时返回 404；
- SQLite 绝对 `log_path` 指向真实外部文件时 compile-log 返回 404；
- `pdf_name=source.pdf` 且文件位于受控 job 目录时返回 200；
- 缺失 desktop job 的旧 artifact body fallback 仍返回 200。

## 完整验证

```text
focused Task 5: 50 passed in 22.46s
Task 5 + related scripts: 59 passed in 26.78s
Task 1–5 full regression: 150 passed in 57.36s
py_compile: exit 0
```

编译文件：

```text
backend/api_v2.py
backend/storage/learning_repository.py
backend/tests/test_learning_storage.py
backend/scripts/test_agent_import.py
backend/scripts/test_paused_history_resume.py
```

## 修改文件

- `backend/api_v2.py`
- `backend/tests/test_learning_storage.py`
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-5-fix-round-2-report.md`

## SHA-256

- `backend/api_v2.py`: `63A63F914B1BEB350A26B2AD56A3A853B8E3A3FE88BB7E8BE87DE258C33FE55E`
- `backend/tests/test_learning_storage.py`: `2A7E555F0614BD8F988E5FBE14CA9427B1E16286AC8C6B7A6E6D25E296C03D68`

`backend/storage/learning_repository.py` 本轮未修改，仍为：
`2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`。

## 剩余关注

- 真实阿里云 MySQL、对象存储和多实例共享文件系统仍属于后续部署任务，与本轮本地路径读取修复无关。
- 建议对本轮两个改动文件进行 fresh scoped re-review 后再同步正式源码。


