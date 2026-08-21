# Task 5 修复轮次 4 独立最终复审

## Verdict

**APPROVED**

轮次 3 确认的同根跨 job junction 越权读取已关闭。本轮只读复核未发现新的确认缺口；Task 5 可以同步到正式源码。

## 安全边界复核

`backend/api_v2.py` 的 `_controlled_source_pdf_job_root()` 现在按以下顺序约束资源目录：

1. job ID 必须能映射为总根下的单层安全名称，空值、`.`、`..` 拒绝；
2. `_SOURCE_PDF_ROOT` 先 canonical 化；
3. job 的词法路径必须是 canonical 总根的直接子目录；
4. job 路径解析后必须与词法路径完全相等，并且父目录仍是 canonical 总根。

第 4 项同时拒绝了两类 reparse 跳转：job A 指向总根外部，以及 job A 指向同根 job B。普通真实目录的 `resolved == lexical`，未被误伤。

文件级 helper 仍要求候选文件解析后的父目录等于已经验证的 job 根。因此文件 symlink 指向 job 根外部时返回 `None`，source-PDF、compile-log、locator 路由均不会读取外部内容。

## 独立复现结果

本机独立创建真实 Windows junction 的探针结果：

```text
MKLINK_RC=0
REALPATH_EQ=True
MARKER=B
TARGET_SURVIVES=True
```

这确认回归测试能够走真实 junction 分支，而不是 `Path.resolve()` 替身。随后亲跑同根跨用户用例：

```text
tests/test_learning_storage.py::test_web_job_junction_to_sibling_job_cannot_cross_resource_ownership PASSED
1 passed in 3.63s
```

用例验证：

- owner A 持有数据库 job A，owner B 持有 job B；
- 磁盘 job A 是指向同根 job B 的真实 junction；
- A 的 PDF、compile-log、locator 均为 404；
- B 自身的三条路由均为 200，并返回 B 目录中的内容。

定向复核另确认：

- job A junction 指向总根外部时，PDF/log/locator 均为 404；
- `.`、`..` 拒绝，普通直接子目录接受；
- 文件 symlink 解析到 job 根外部的路由替身探针为 `404`，`SECRET_LEAK=False`；当前账户不能创建真实文件 symlink（系统返回权限不足），因此采用与 `Path.resolve()` 相同结果的可控替身；
- 11 条统一 job 资源路由继续对其他用户返回 404；
- 重启后的持久化 artifact 导出继续忽略客户端注入，使用数据库 JSON 降级导出。

相关定向测试：

```text
14 passed in 10.77s
```

## 完整验证

在 writable mirror 的 `backend` 目录亲自运行：

```text
.\.venv\Scripts\python.exe -m pytest tests\test_learning_storage.py -q
53 passed in 48.37s

.\.venv\Scripts\python.exe -m pytest tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py -q
62 passed in 52.91s

.\.venv\Scripts\python.exe -m pytest tests\test_database_models.py tests\test_auth_mysql.py tests\test_admin_authorization.py tests\test_admin_user_import.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py -q
153 passed in 95.72s

.\.venv\Scripts\python.exe -m py_compile api_v2.py storage\learning_repository.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
exit 0
```

## 复核哈希

- `backend/api_v2.py`: `87C25D81330ACA4CF4497BB0F91C2E62340A057D68FAEBB02512D4BC0A899B41`
- `backend/tests/test_learning_storage.py`: `1E4DC1A5502414FCC47BA3B7FA92E1A9CB4A2BBBC9D601558D4DE6E1608FD51B`
- `backend/storage/learning_repository.py`: `2961C7F9E67DCBF11099B92A4D2A889F7FA90961591B581BAC8D0DA4CD7F6974`

## 已知限制

- 路径检查与 `send_file` 之间仍存在理论上的文件系统 TOCTOU 窗口；当前威胁模型下，普通 Web 用户不能写服务器的 source-PDF 目录，本轮不将其视为发布阻断项。
- 真实阿里云 MySQL、对象存储与多实例文件共享仍属于后续部署验证，不影响本轮 Task 5 的 junction 所有权修复结论。

## 最终结论

轮次 4 的最小修复正确要求 `resolved_job_root == lexical_job_root`，保留外部 junction、文件级 containment、Web ownership 和数据库 artifact fallback 的既有安全边界。所有指定测试和编译检查均为 fresh 通过，结论为 **APPROVED**。

