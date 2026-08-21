# Task 3 修复轮次 1 独立复审

## 结论

原审查中唯一的高严重度 TOCTOU 并发问题已关闭，未发现本轮引入的回归。

- Addressed: 1
- Open: 0
- Verdict: **APPROVED**

## 原问题复核

### 已解决：认证状态按目标用户行串行化

`AuthRepository.user_transaction_by_identifier(...)` 与
`AuthRepository.user_transaction_by_id(...)` 都在 `session_scope()` 管理的同一
SQLAlchemy `Session` 中查询目标用户，并对查询应用 `with_for_update()`。
`session_scope()` 在上下文正常退出后提交，异常时回滚，最后关闭 Session；因此
MySQL/InnoDB 中的用户行锁会覆盖整个认证状态决策和写入阶段，而不是只覆盖查询。

`AuthUserTransaction` 的写操作全部复用持锁 Session：

- `insert_session()` 在登录持锁期间插入会话；
- `has_active_session()` 在改密持锁期间校验当前会话；
- `replace_password()` 在同一事务中更新密码和提示状态、撤销全部旧会话，并可插入唯一替代会话；
- `set_active_status()` 在同一事务中更新启用状态，停用时同步撤销全部会话。

Service 调用链也已完整收进这一边界：

- 登录的 active/密码校验和会话插入全部位于 `user_transaction_by_identifier()` 内；
- 改密的 active、当前 session、当前密码校验，以及密码写入、旧 session 撤销、新 session 插入全部位于 `user_transaction_by_id()` 内；
- 管理员重置的密码写入与 session 撤销位于同一目标用户事务内；
- 状态变更及停用时的 session 撤销位于同一目标用户事务内。

锁定的 ORM `User` 仅在事务上下文中用于安全决策；Service 在离开上下文前将其转换为冻结的
`AuthenticatedUser` 值对象，未向 API 或调用者泄漏持锁实体。登录、改密、重置和状态变更均在
事务提交完成后才返回结果；异常路径由 `session_scope()` 回滚。

## 并发语义复核

实现不依赖 Python 进程锁。测试中的 `threading.Lock` 仅是可控事务替身，用来模拟数据库行锁的
排序行为；生产代码的跨 worker 串行化边界是 MySQL `SELECT ... FOR UPDATE`。

两个仓储入口生成的 SQL 均由 SQLAlchemy MySQL 方言亲自编译检查，结果包含
`SELECT ... FOR UPDATE`。在 MySQL/InnoDB 中，同一用户上的执行顺序具有合理的线性化语义：

- 两个并发改密中，先提交者撤销全部旧会话并替换密码；后取得锁者会从新状态检查 session/旧密码并被拒绝，不能都基于旧状态成功；
- 改密与管理员重置按锁获取顺序执行。后执行的重置可合法覆盖先完成的改密并撤销其新会话；若重置先执行，后续改密会因旧 session/旧密码失效而拒绝；
- 登录先执行时，会话先提交，后续停用/重置会将其撤销；停用/重置先执行时，后续登录读取新状态并拒绝旧凭据；
- 较早、基于旧状态的事务不可能在较晚撤销事务提交后重新插入有效旧凭据会话。

SQLite 功能测试没有被描述为真实 MySQL 行锁证明。修复测试明确分开了两层验证：MySQL 方言
SQL 生成验证，以及可控替身上的服务排序验证；完整功能合同继续在隔离 SQLite 上回归。

## 顺序 API 合同

顺序 API 行为仍保持：密码策略、错误码、精确用户形状、改密后单一新 token、管理员重置临时密码、
停用撤销会话、自停用保护、401/403 区分及敏感值不入日志的既有测试全部通过。未发现本轮修复改变
路由响应合同或跨越 Task 3 范围。

## 亲自运行的验证

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
# 50 passed in 14.00s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
# 72 passed, 7 warnings in 17.52s

.\.venv\Scripts\python.exe -m py_compile services\auth_service.py storage\auth_repository.py api_v2.py tests\test_auth_mysql.py tests\test_admin_authorization.py
# exit 0，无输出
```

七个 warning 仍来自 `api_v2.py` 旧历史/任务路径中的 `datetime.utcnow()` 弃用提示，不是本轮新增。

## 验证限制

本轮没有真实阿里云 MySQL 连接，因此没有执行真实 InnoDB 多连接并发烟雾测试。当前证据覆盖了
MySQL `FOR UPDATE` SQL 生成、生产事务边界的静态审查、可控并发排序以及全部顺序回归；真实 MySQL
并发烟雾验证仍应在部署阶段执行，但不构成本轮修复的开放缺陷。

Addressed: 1；Open: 0；Verdict: APPROVED
