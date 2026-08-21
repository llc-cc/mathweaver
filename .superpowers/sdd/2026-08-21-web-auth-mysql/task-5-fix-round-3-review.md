# Task 5 修复轮次 3 独立最终审查

## Verdict

**CHANGES REQUIRED**

外部 junction 逃逸已经修复，但同一 `_SOURCE_PDF_ROOT` 内的跨 job junction 仍会绕过任务归属边界。该问题已用真实 Windows junction 和两名 Web 用户复现，会同时泄露另一任务的 PDF、编译日志和定位所读取的 TeX 内容，因此本轮不能批准。

## 已确认通过

- 真实 Windows junction 将 job 目录指向总根外部时，source-PDF、compile-log、locator 均返回 404。
- 安全 UUID job 目录中的 PDF、日志均返回 200，locator 只从受控目录中的 `source.tex` 提取标题。
- `""`、`.`、`..` 不会成为 job 根；`../`、`a/b`、`a\\b` 会被安全化为总根下的普通直接子项，不会解析成总根或父目录。
- 旧 SQLite 元数据中的普通绝对 `pdf_path` 不会直通，路由返回 404，未泄露外部内容。
- 文件级 symlink 的代码边界仍为 `candidate.resolve().parent == root`；当前 Windows 账户创建真实文件 symlink 时返回 `WinError 1314`，因此本轮无法重复真实文件 symlink 探针，但该分支未被轮次 3 修改，且静态检查未发现回退。
- 现有 Web 未登录 401、其他用户 404、持久化 artifact 忽略客户端注入并使用数据库 JSON 的回归均通过。

独立外部 junction/安全目录探针结果：

```text
REAL_JUNCTION_CREATED=True
JUNCTION_STATUSES=404 404 404
SAFE_STATUSES=200 200 200
SAFE_CONTENT=True True
SAFE_LOCATOR_TERMS=['INSIDE CONTROLLED']
ABSOLUTE_STATUS=404 LEAK=False
JOB_ID ''   -> None
JOB_ID '.'  -> None
JOB_ID '..' -> None
JOB_ID '../', 'a/b', 'a\\b' -> 总根下普通直接子项，均非总根/父目录
```

## High：同根跨 job junction 可造成跨用户文件读取

文件：`backend/api_v2.py:3714-3727`

当前 `_controlled_source_pdf_job_root()` 只验证：

```python
resolved_job_root.parent == canonical_root
```

这能拒绝解析到总根外部的 junction，却会接受 job A 指向同一总根下 job B 的 junction。此时 `_owned_job_resource("job-a")` 正确验证了调用者拥有 job A，但后续 source-PDF 路径解析实际进入 job B，任务所有权检查与文件所有权发生错位。

使用真实 Windows junction 和两个不同 Web 用户的独立复现结果：

```text
<source_pdf_root>/job-a  ->  <source_pdf_root>/job-b
job-a belongs to user A
job-b belongs to user B

REAL_SIBLING_JUNCTION_CREATED=True
OWNER_A_ROUTE_STATUSES=200 200 200
CROSS_USER_PDF_LEAK=True
CROSS_USER_LOG_LEAK=True
CROSS_USER_LOCATOR_TERMS=['USER B PRIVATE TITLE']
```

这不是客户端 body 回退，而是已认证 owner A 通过自己的 job ID 读取 owner B 的磁盘资源，直接违反 Web 任务归属边界。

建议要求 job 根解析后不仅父目录相同，而且必须仍是该 job 的词法路径，例如在 Windows 路径语义下验证：

```python
if resolved_job_root != lexical_job_root:
    return None
```

随后增加真实 junction 回归：job A 指向同根 job B 时，PDF、log、locator 均应 404。原有“指向根外”测试也必须保留。

## 独立验证

在 mirror 的 `backend` 目录亲自运行：

```text
.\.venv\Scripts\python.exe -m pytest tests\test_learning_storage.py -q
52 passed in 21.84s

.\.venv\Scripts\python.exe -m pytest tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py -q
61 passed in 24.47s

.\.venv\Scripts\python.exe -m pytest tests\test_database_models.py tests\test_auth_mysql.py tests\test_admin_authorization.py tests\test_admin_user_import.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py -q
152 passed in 53.65s

.\.venv\Scripts\python.exe -m py_compile api_v2.py storage\learning_repository.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
exit 0
```

测试全绿，但现有测试仅覆盖 junction 指向总根外部，没有覆盖指向同根其他 job 的跨所有权场景，所以不能据此批准。

复核 SHA-256：

- `backend/api_v2.py`: `5BBCE6BC793CE8BF2A8FFE97839817167F98776730EA0B82325E5241F482898A`
- `backend/storage/learning_repository.py`: `2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`
- `backend/tests/test_learning_storage.py`: `5E5468553B0E2200EB75F71B07138EDB6B42807DB621C83211401CE33E637A7F`

## 最终结论

轮次 3 已关闭“job 根指向总根外部”的漏洞，危险 job ID、绝对路径和常规 Web/artifact 回归也未退化；但真实同根 sibling junction 仍可把 owner A 的合法资源请求重定向到 owner B 的文件。修复跨 job junction、补负向回归并 fresh re-review 前，Task 5 不应同步到正式源码。

