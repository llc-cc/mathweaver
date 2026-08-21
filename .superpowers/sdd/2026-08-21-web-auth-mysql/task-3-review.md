# Task 3 独立代码审查

## 结论

**CHANGES REQUESTED — 未批准。**

顺序执行时，密码修改、管理员重置、账号状态、角色鉴权、错误码和敏感值处理均符合任务契约，聚焦测试与 Task 1–3 合并回归也全部通过；但认证状态的“检查”和“写入”分布在多个独立事务中，存在可利用的并发竞态。当前开放问题共 **1** 个：高 1 个。

## 问题（按严重级别）

### 高（阻止批准）1：登录、改密、重置与停用未按同一用户串行化，旧凭据会话可能在撤销后重新存活

- 位置：`backend/services/auth_service.py:80-95`、`backend/services/auth_service.py:110-162`、`backend/storage/auth_repository.py:25-52`、`backend/storage/auth_repository.py:102-156`
- `login()` 先在一个仓储事务中读取并验证用户，之后再用另一个事务插入会话；`change_password()` 也先分别认证会话、读取用户并验证旧密码，最后才在第三个事务中更新密码、撤销会话和插入新会话。管理员重置与账号停用虽然各自把本次写入和撤销放在一个事务里，但它们没有与登录或其他密码写入共享用户行锁/版本条件。
- 可复现的数据库交错不需要单个事务失败：
  1. 登录请求 A 读取到旧密码且账号仍启用；
  2. 请求 B 完成改密或停用，并撤销当时已经存在的全部会话；
  3. 请求 A 在 B 提交后才插入新会话并成功返回。
  此时旧密码生成的会话在改密/停用完成后仍然有效，直接破坏“所有旧会话失效”和停用账号立即失效的安全边界。
- 两个并发改密/“改密与管理员重置”请求也都可能基于同一旧密码通过预检，随后互相覆盖密码并撤销对方刚创建的新会话。于是两个请求都返回成功，但其中一个响应里的新 token 或临时密码可能立即不可用；并发结果取决于提交顺序。
- `update_password_and_sessions()` 内部的密码写入、会话撤销和替代会话插入确实是一个事务，但它开始得太晚，没有包含旧密码、账号状态和当前会话的条件检查，因此不能消除上述 TOCTOU 竞态。现有测试全部为顺序 SQLite 测试，也未覆盖这一任务明确要求审查的事务一致性边界。
- 要求：按用户将登录、改密、重置和状态变更串行化。可在同一事务中对目标 `users` 行做 `SELECT ... FOR UPDATE`，并确保登录的密码/启用状态校验与会话插入也位于持锁事务内；改密则应在同一持锁事务中验证当前会话、旧密码，更新哈希、撤销旧会话并创建唯一替代会话。也可采用等价的乐观版本/CAS 方案，但必须保证并发操作只能有一个基于旧状态成功。补充可控并发回归，至少证明：旧密码登录与改密竞态不会留下有效旧凭据会话；登录与停用竞态不会留下有效会话；两个并发密码写入不会都返回可误导的成功结果。

## 已确认符合规格的部分

- `initial_password_pending` 仅出现在认证用户形状中，没有参与历史或工作区入口鉴权；`GET /api/v2/history` 在该标记为真时返回 200。
- 修改密码校验当前密码，并执行 8–128 位（含边界）的统一长度策略；顺序成功路径会清除提示标记、只保存 Werkzeug 哈希、撤销两个旧 token、返回可用的新 token，旧密码无法再次登录。
- 管理员重置使用 `secrets.token_urlsafe(12)` 生成临时密码，只持久化 Werkzeug 哈希，将提示标记设为真并撤销目标用户已有会话；临时密码只由当前响应返回。检索未发现新代码记录密码、临时密码、原始 Bearer token、哈希或数据库凭据。
- `require_role()` 在路由入口强制角色授权：缺失/无效会话为 401，已认证非管理员为 403；Service 层也再次保护管理员业务规则。
- 管理员无法停用自己；停用其他账号的状态写入与当时已有会话撤销处于同一仓储事务。
- 改密和状态接口对非对象 JSON、缺字段和非字符串/非布尔类型稳定返回 400；未知管理目标返回 404，没有发现这些输入穿透成 500。
- API 返回的用户对象保持要求的六字段精确形状；Repository 不解析 Flask 请求，Service 不依赖请求对象，分层边界清晰。
- 未发现 CSV 导入、前端、历史迁移或 Task 4+ 功能被越界实现。
- 实现报告给出的三个 SHA-256 与当前文件一致：
  - `backend/services/auth_service.py`: `126C97BF345667D35D625C0B3DCBA0061536E3041CB6201EDD84304C266E12A3`
  - `backend/storage/auth_repository.py`: `0BAB871D97ED32C74EFD17A1DFE877D8980E36ABAC451E7FC97F1F7C6AC22CBF`
  - `backend/api_v2.py`: `FC6BA2EA28A7493E35819140CE31732BE5C8A29D60BB6C889F98EFD66FDC97E8`

## 亲自运行的验证

在实现目录 `backend` 下运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
# 46 passed in 13.33s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
# 68 passed, 7 warnings in 17.20s

.\.venv\Scripts\python.exe -m py_compile services\auth_service.py storage\auth_repository.py api_v2.py tests\test_auth_mysql.py tests\test_admin_authorization.py
# exit 0
```

七个警告仍是 `api_v2.py` 旧历史/任务路径中的 `datetime.utcnow()` 弃用警告，不是 Task 3 新增问题。

## 已知验证限制

- 当前没有连接真实阿里云 MySQL，测试运行于隔离 SQLAlchemy SQLite；因此无法用现有测试环境直接执行 MySQL 行锁级并发用例。但上述竞态由跨事务调用结构直接产生，不依赖 SQLite 特性，必须在批准前修复并增加可控并发验证。
