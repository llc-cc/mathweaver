# Task 2 交付报告

## 状态

完成。Web 认证已切换到 SQLAlchemy 仓储与安全会话服务；公开注册在 Web 模式返回 404，桌面模式在未配置数据库 URL 时仍保留旧 SQLite 兼容路径。

## 创建/修改文件

创建：

- `backend/storage/auth_repository.py`
- `backend/services/__init__.py`
- `backend/services/auth_service.py`
- `backend/tests/test_auth_mysql.py`

修改：

- `backend/tests/conftest.py`
- `backend/api_v2.py`
- `backend/scripts/test_agent_import.py`
- `backend/scripts/test_paused_history_resume.py`

## RED 记录

在任何生产代码改动前，于 `backend` 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py -q
```

有效 RED 结果：退出码 1，`16 failed`。预期失败证据包括：

- 学号/邮箱登录仍返回 401，而非 200；
- 公开注册仍返回 201，而非 404/405；
- 错误消息仍为旧英文消息；
- `storage.auth_repository` 与 `services.auth_service` 尚不存在；
- 未配置 `MATHWEAVER_DATABASE_URL` 的 Web 导入仍以退出码 0 成功。

首次运行同一命令时，8 个 API 测试还被无关的可选 `dashscope` 导入错误阻断；仅在测试侧加入 `JoinAgent` 轻量占位后重跑，得到上述只由 Task 2 缺失导致的有效 RED。此过程中未改生产代码。

## GREEN 与回归验证

于 `backend` 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py -q
```

结果：`18 passed in 8.69s`。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -q
```

结果：`12 passed in 1.07s`。

```powershell
.\.venv\Scripts\python.exe -m pytest scripts/test_agent_import.py -q
```

结果：`5 passed, 2 warnings in 2.74s`。

```powershell
.\.venv\Scripts\python.exe -m pytest scripts/test_paused_history_resume.py -q
```

结果：`4 passed, 5 warnings in 3.71s`。

```powershell
.\.venv\Scripts\python.exe -m py_compile storage\auth_repository.py services\auth_service.py api_v2.py tests\test_auth_mysql.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
```

结果：退出码 0。

已检查所有 `backend/scripts/test_*.py` 中的 `/auth/register` 调用；两处旧夹具均改为直接创建 SQLAlchemy 测试账号后调用登录。当前唯一剩余调用位于 `tests/test_auth_mysql.py`，用于断言 Web 公开注册返回 404/405。

## SHA-256

- `backend/storage/auth_repository.py`: `AC728E258427FD047C364AD417C05519040E8FB0D0A3DF06F0381545A435B4A9`
- `backend/services/auth_service.py`: `672B5323304D70AD30AB5612098100481C0385C1F7A6F64B9020261D8EACF3FC`
- `backend/api_v2.py`: `85F7F7529813E5C9F4512121BBDCD1E33BDFE5E3F46F46346266014274BEDB17`

## 自审

- 仓储按是否含 `@` 确定唯一查询分支：邮箱去空白并转小写，学号去空白后精确匹配并保留前导零。
- 登录统一处理未知账号、错误密码和停用账号，使用 Werkzeug 校验密码哈希。
- 原始令牌由 `secrets.token_urlsafe(32)` 生成，仅 SHA-256 摘要进入 SQLAlchemy 会话表。
- 有效会话同时校验未撤销、未过期和用户启用，并在成功认证后更新 `last_used_at`。
- 注销只撤销当前摘要且可重复调用；批量撤销接口支持保留一个摘要。
- TTL 默认 604800 秒；非整数、零和负值会在服务构造/应用启动时失败。
- Web 启动缺少数据库 URL 会失败；只有 `AI4MATH_DESKTOP=1` 且缺少 URL 时进入旧 SQLite 兼容分支。
- 未实现密码重置、密码修改、管理员导入，也未迁移历史、设置或证明路由。

## 关注与已知限制

- 按任务裁定，桌面无数据库 URL 模式仍保留旧 SQLite 认证，包括旧式会话存储和公开注册；安全令牌改造仅适用于新的 Web 认证路径。
- 历史、设置和证明路由仍使用旧 SQLite；Web 用户仅通过内部映射形状与这些路由兼容，完整持久化迁移属于后续任务。
- 本任务使用显式 SQLAlchemy SQLite URL 做隔离测试，未连接真实 MySQL 实例。
- 两份旧脚本仍报告既有 `datetime.utcnow()` 弃用警告；这些调用位于本任务未迁移的历史/任务代码中。

---

# Task 2 修复轮次 1

## 状态与变更文件

审查提出的 3 项问题均已修复。

本轮修改：

- `backend/api_v2.py`
- `backend/storage/models.py`
- `backend/migrations/versions/20260821_01_web_auth_mysql.py`
- `backend/tests/test_auth_mysql.py`
- `backend/tests/test_database_models.py`
- `backend/scripts/test_agent_import.py`
- `backend/scripts/test_paused_history_resume.py`

## RED 记录

### 合并顺序依赖

修复测试夹具前，于 `backend` 运行审查指定命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

结果：退出码 1，`5 failed, 34 passed, 1 warning`。首个失败为向当前全局引擎写入用户时出现 `sqlite3.OperationalError: no such table: users`；后续失败同源，并伴随 Windows 临时 SQLite 文件清理风险。

### 登录请求类型校验

先增加 JSON 数组、数字 `identifier`、数字 `password` 三个边界用例，再运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py -k "login_rejects_non" -q
```

结果：退出码 1，`3 failed, 19 deselected`。数组返回 401，数字 `identifier` 返回 500 并在 `.strip()` 触发 `AttributeError`，数字 `password` 返回 401，均未满足稳定 400 合同。

### MySQL 学号大小写语义

先增加 MySQL 模型/迁移 DDL 行为测试，再运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -k "case_sensitive_student_numbers" -q
```

结果：退出码 1，`1 failed, 12 deselected`；编译后的 MySQL DDL 中 `student_no VARCHAR(64)` 未包含 `COLLATE utf8mb4_bin`，会继承表级 `utf8mb4_unicode_ci`。

## GREEN 与最终验证

聚焦 GREEN：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py -k "login_rejects_non or student_number_identifier_is_case_sensitive or email_login_is_case_insensitive" -q
# 5 passed, 17 deselected

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -k "case_sensitive_student_numbers" -q
# 1 passed, 12 deselected
```

相关单文件：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py -q
# 22 passed in 8.64s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -q
# 13 passed in 2.02s

.\.venv\Scripts\python.exe -m pytest scripts/test_agent_import.py -q
# 5 passed, 2 warnings in 3.11s

.\.venv\Scripts\python.exe -m pytest scripts/test_paused_history_resume.py -q
# 4 passed, 5 warnings in 3.63s
```

指定合并命令连续两次运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

结果分别为：

- `44 passed, 7 warnings in 11.52s`
- `44 passed, 7 warnings in 11.88s`

两次均未出现 `WinError 32` 或 SQLite 临时文件句柄残留。

语法检查：

```powershell
.\.venv\Scripts\python.exe -m py_compile storage\auth_repository.py services\auth_service.py storage\models.py api_v2.py migrations\versions\20260821_01_web_auth_mysql.py tests\test_auth_mysql.py tests\test_database_models.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
```

结果：退出码 0。

## 新 SHA-256

- `backend/storage/auth_repository.py`: `AC728E258427FD047C364AD417C05519040E8FB0D0A3DF06F0381545A435B4A9`
- `backend/services/auth_service.py`: `672B5323304D70AD30AB5612098100481C0385C1F7A6F64B9020261D8EACF3FC`
- `backend/storage/models.py`: `ED27FC767FB9E244C7497456328B3B3B2FC2C6342C340D72AE1D1EA659F3CEBB`
- `backend/api_v2.py`: `63CDC0F5F3BDD6CF281BA857B86BE0ECB985DEBD6AD2A8A3C4C395AA99AE17F6`
- `backend/migrations/versions/20260821_01_web_auth_mysql.py`: `8B9E3A13B0A04F0D24AC2BD6FF5A248F1AC4852B7990C7C4A1387FBC72BAFBD1`
- `backend/tests/test_auth_mysql.py`: `E24B867E478103CED88B9BFACD6FD642FAE56BECB22F225E9DE8AFB28179EE1B`
- `backend/tests/test_database_models.py`: `E2007A529E19229589B435FF3A73739E4CCD3CB4E8A7136E63F58E6A2FAB66BD`
- `backend/scripts/test_agent_import.py`: `72A4443BD0677F1B87A726476EA467F6B5610BAE9CFC0FE83F4CACE0247F4415`
- `backend/scripts/test_paused_history_resume.py`: `91519F617880EA371A1AB6131FE48E1F7D0B8C78586E17D9E9EFD90682DDA8B8`

## 三项处置与自检

1. 两份旧脚本不再依赖模块导入时的一次性建表。各自的自动夹具为每个测试重建 SQLAlchemy 内存引擎和表，隔离旧 SQLite 路径与任务字典，并在异常路径执行删表、`Engine.dispose()`、Flask app-context 清理及全局状态恢复。
2. Web 登录路由在调用服务/仓储前验证请求 JSON 必须为对象，且 `identifier`、`password` 必须为字符串；数组返回 `400 {"error":"JSON object required"}`，字段类型错误返回 `400 {"error":"identifier and password must be strings"}`，不再进入 500 路径。
3. `student_no` 在模型和首次 Alembic 迁移中使用 SQLAlchemy MySQL 方言变体 `VARCHAR(64) COLLATE utf8mb4_bin`；SQLite 仍使用普通 `String(64)`，因此测试兼容。行为测试确认 `Ab01` 不会被 `ab01` 命中；邮箱仍先标准化为小写并继承表级 `utf8mb4_unicode_ci`。

## 本轮已知限制

- 合并回归仍显示 7 个既有 `datetime.utcnow()` 弃用警告，调用点位于未迁移的历史/任务代码，不属于本轮三个审查问题。
- MySQL 语义通过 SQLAlchemy MySQL DDL 与 Alembic 离线 DDL 验证，仍未连接真实 MySQL 实例。
