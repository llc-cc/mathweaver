# Task 5 修复轮次 4 报告

## 状态

`task-5-fix-round-3-review.md` 的同根跨 job junction 文件读取问题已修复。仅修改 writable mirror，未修改正式源码、未提交 Git。

## TDD 证据

生产代码修改前新增真实 Web 所有权回归：

- 用户 A 拥有数据库任务 `job-a`；
- 用户 B 拥有数据库任务 `job-b`；
- `_SOURCE_PDF_ROOT/job-a` 是指向同根 `job-b` 的真实 Windows junction；
- job B 目录包含私有 PDF、compile log 和 TeX source；
- 用户 A 通过自己的 job A 请求 PDF、log、locator；用户 B 正常请求 job B。

若运行环境拒绝创建 junction，测试会通过可控 `Path.resolve()` 替身复现相同的同根解析结果。

RED：

```text
1 failed
owner A statuses: 200, 200, 200
```

说明数据库所有权验证虽然正确，但文件根被 junction 重定向到 user B 的 job 目录。

最小修复后同一测试 GREEN：

```text
1 passed in 2.85s
```

## 修复内容

`_controlled_source_pdf_job_root()` 现在同时要求：

```python
resolved_job_root == lexical_job_root
resolved_job_root.parent == canonical_root
```

因此：

- job 目录 junction/symlink 指向总根外部：拒绝；
- job A junction/symlink 指向同根 job B：拒绝；
- 普通真实 job 目录：接受；
- `_SOURCE_PDF_ROOT` 自身仍先 canonical 化，部署方配置的总根路径语义保持不变。

source-PDF、compile-log、locator 继续统一经过该 job-root helper 和文件级 containment helper。Web `_owned_job_resource()` 的 401/404/数据库 owner 行为没有修改。

## 安全回归结果

- owner A 请求 junction job A 的 PDF/log/locator：`404 / 404 / 404`；
- owner B 请求自己的普通 job B：`200 / 200 / 200`；
- job B PDF 与 log 响应内容与受控目录文件一致；
- 上轮 job 根指向总根外部、`.`/`..`、绝对路径和普通安全目录测试继续通过。

## 完整验证

```text
focused Task 5: 53 passed in 27.68s
Task 5 + related scripts: 62 passed in 32.72s
Task 1–5 full regression: 153 passed in 45.88s
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
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-5-fix-round-4-report.md`

## SHA-256

- `backend/api_v2.py`: `87C25D81330ACA4CF4497BB0F91C2E62340A057D68FAEBB02512D4BC0A899B41`
- `backend/tests/test_learning_storage.py`: `1E4DC1A5502414FCC47BA3B7FA92E1A9CB4A2BBBC9D601558D4DE6E1608FD51B`

`backend/storage/learning_repository.py` 本轮未修改：
`2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`。

## 剩余关注

- 后续路径改动必须保留 `canonical total root → lexical exact job root → resolved exact equality → file containment` 顺序。
- 真实阿里云 MySQL、对象存储和多实例文件共享仍属于后续部署任务。
- 建议 fresh scoped re-review 后再同步正式源码。


