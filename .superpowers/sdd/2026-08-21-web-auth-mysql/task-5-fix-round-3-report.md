# Task 5 修复轮次 3 报告

## 状态

`task-5-fix-round-2-review.md` 的 job 目录 junction 逃逸问题已修复。仅修改 writable mirror，未修改正式源码、未提交 Git。

## TDD 证据

生产代码修改前新增两类测试：

1. `.` / `..` job ID 必须拒绝，普通 `safe-job` 直接子目录必须接受；
2. 优先创建真实 Windows junction，将受控 job 目录指向外部目录，并通过 source-PDF、compile-log、locator 三个路由尝试读取。若运行环境拒绝 junction 创建，测试使用可控路径替身复现相同的“词法根在内、resolve 后在外”条件。

RED：

```text
2 failed, 50 deselected
```

实际失败行为：

- `.` 被解析为整个 `_SOURCE_PDF_ROOT`；
- junction 下外部 PDF 返回 200。

实现后同一测试 GREEN：

```text
2 passed, 50 deselected in 2.44s
```

## 修复内容

### Canonical job root

新增 `_controlled_source_pdf_job_root(job_id)`：

- 首先将 job ID 安全化为 `[A-Za-z0-9_.-]` 单一目录段；
- 拒绝空字符串、`.`、`..` 或非单一 basename；
- 先计算 canonical `_SOURCE_PDF_ROOT.resolve()`；
- 未解析的 job 根必须在词法上是 canonical root 的直接子项；
- 再解析 job 根，解析后的 `resolved_job_root.parent` 仍必须等于 canonical root；
- job 目录自身是 junction/directory symlink 且指向外部时因此返回 `None`。

### Canonical file path

`_controlled_source_pdf_file()` 只接受已验证的 canonical job root：

- 文件候选在词法上必须是该 job root 的直接子项；
- 文件 `resolve()` 后的 parent 仍必须等于该 job root；
- 文件 symlink 指向外部时被拒绝。

### 三个资源入口统一

`_source_pdf_context()` 在处理 PDF/source/log 前先验证 job root；失败时直接返回无资源。因此：

- `GET /api/v2/source-pdf/<job_id>`：junction 逃逸返回 404；
- `GET /api/v2/source-pdf/<job_id>/compile-log`：junction 逃逸返回 404；
- `GET /api/v2/source-pdf/<job_id>/locate`：不会读取 junction 外部 TeX，也返回 404。

普通安全 job 目录、上一轮安全 basename desktop 回退、Web 鉴权/404/所有权规则均保持不变。

## 验证

```text
focused Task 5: 52 passed in 21.69s
Task 5 + related scripts: 61 passed in 25.75s
Task 1–5 full regression: 152 passed in 61.94s
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
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-5-fix-round-3-report.md`

## SHA-256

- `backend/api_v2.py`: `5BBCE6BC793CE8BF2A8FFE97839817167F98776730EA0B82325E5241F482898A`
- `backend/tests/test_learning_storage.py`: `5E5468553B0E2200EB75F71B07138EDB6B42807DB621C83211401CE33E637A7F`

`backend/storage/learning_repository.py` 本轮未修改：
`2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`。

## 剩余关注

- 路径验证采用“canonical 总根 → 词法直接 job 子项 → resolve 后仍为直接子项 → 文件 resolve 后仍属于 job 根”的双层 containment；后续修改路径逻辑时必须保留这个顺序。
- 真实阿里云 MySQL、对象存储和多实例共享文件系统仍属于后续部署任务。
- 建议进行 fresh scoped re-review 后再同步到正式源码。


