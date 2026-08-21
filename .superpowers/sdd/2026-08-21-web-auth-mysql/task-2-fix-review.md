# Task 2 修复轮次 1 独立复审

## 结论

**APPROVED — 批准进入后续任务。**

原审查的 3 项问题均已正确解决，限定范围内未发现明显回归。

## 三项复审结果

### 1. 合并测试顺序、数据库隔离与句柄释放

**已解决。**

- `scripts/test_agent_import.py` 与 `scripts/test_paused_history_resume.py` 均改为自动夹具按测试重新配置 SQLAlchemy 内存库并创建表，不再依赖模块导入时的一次性建表。
- 两个夹具均在 `finally` 中执行 `Base.metadata.drop_all(test_engine)`、`test_engine.dispose()`，并恢复数据库模块的引擎/会话工厂、任务字典和运行时字典；`_DB_PATH` 由 `monkeypatch` 自动恢复。
- 旧 SQLite 请求连接由 Flask teardown 关闭，夹具退出分支也显式调用 `_close_db()`。脚本内部 `TemporaryDirectory` 在测试结束前完成清理，连续两次合并运行均未出现 `WinError 32` 或残留句柄错误。
- 规定的合并命令连续两次均得到 `44 passed, 7 warnings`，未再出现 `no such table: users`。

### 2. 登录请求体和字段类型校验

**已解决。**

- Web 登录路由在调用认证服务和仓储前先要求请求 JSON 为对象，否则稳定返回 `400 {"error":"JSON object required"}`。
- `identifier` 或 `password` 不是字符串时稳定返回 `400 {"error":"identifier and password must be strings"}`。
- 回归测试覆盖 JSON 数组、数字 `identifier` 和数字 `password`；合并套件全部通过，未进入 500 异常路径。

### 3. MySQL 学号精确匹配语义

**已解决。**

- `storage/models.py` 的 `User.student_no` 使用 MySQL 方言变体 `VARCHAR(64) COLLATE utf8mb4_bin`，SQLite 仍使用普通 `String(64)`，测试环境兼容。
- 首次 Alembic 迁移中的同一列使用一致的 MySQL 方言变体；模型生成的 MySQL DDL 与 Alembic 离线 DDL 均由测试断言包含 `utf8mb4_bin`。
- SQLite 行为测试确认 `Ab01` 只能由完全相同的学号命中，`ab01` 不会命中；邮箱列未改为二进制排序，登录仍先去空白并转小写，原有不区分大小写规则保持。

## 亲自执行的验证

在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

首次结果：`44 passed, 7 warnings in 11.53s`，退出码 0。

为确认顺序稳定性再次执行同一命令，结果：`44 passed, 7 warnings in 11.71s`，退出码 0。

执行语法编译：

```powershell
.\.venv\Scripts\python.exe -m py_compile storage\auth_repository.py services\auth_service.py storage\models.py api_v2.py migrations\versions\20260821_01_web_auth_mysql.py tests\test_auth_mysql.py tests\test_database_models.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
```

结果：退出码 0，无输出。

7 条警告均为既有历史/任务代码中的 `datetime.utcnow()` 弃用警告，与本轮三项修复无关。

## 汇总

- Addressed: 3
- Open: 0
- Verdict: APPROVED
