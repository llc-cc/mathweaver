# Task 6 独立审查报告

## 结论

**CHANGES REQUIRED**

现有 15 项聚焦测试和 Task 1–6 的 165 项精确回归均通过，但独立探针确认两条 Task 6 核心验收语义仍可在错误数据上退出 `0`；另有一处中心库路径信息泄露边界未处理。以下问题修复并补回归测试后才能批准。

## Findings

### [P1] 备份与实际迁移数据不是同一个 SQLite 快照

位置：`backend/scripts/migrate_sqlite_to_mysql.py:360-368`

非 dry-run 先从源库读取 `counts/data`，关闭该读取流程后才创建 online backup，最后仍把第一次读取的数据写入目标库。若桌面程序或其他进程在“读取完成”和“备份开始”之间提交写入，备份会包含新数据，而目标库迁移的是旧数据；脚本仍会用旧 `counts` 核对旧数据并退出 `0`。

独立探针在 `_load_source()` 返回后、`_create_backup()` 前提交第 3 个用户，结果为：

```text
exit=0 backup_users=3 target_users=2
```

这不满足“迁移前备份”和可恢复性要求：成功消息所对应的目标数据无法由该备份精确解释。建议非 dry-run 先用 read-only online backup 固化快照，再只从备份读取、解析和迁移；dry-run 继续直接读取原库且不创建备份。增加并发边界测试，断言备份计数、实际导入计数和核验基准始终来自同一快照。

### [P1] 只核对行数，没有在提交前核验目标外键

位置：`backend/scripts/migrate_sqlite_to_mysql.py:326-334`

`_load_source()` 会检查源数据的 `user_id`，但目标写入后只比较表行数和 session 数，没有查询目标 `history`、`user_settings`、`proof_workspaces` 是否仍全部引用存在的用户。这不满足计划中“exit code 0 only when row counts and foreign-key references match”的公开接口。

独立 SQLite 目标探针用 `AFTER INSERT` trigger 将导入后的 `history.user_id` 改为不存在的 `999`。四张业务表行数仍全部匹配，脚本输出 `migration completed and row counts verified` 并退出 `0`，但目标查询得到：

```text
history_user_id=999
missing_parent_count=1
```

即使生产 MySQL 通常由 InnoDB 外键拦截，迁移脚本仍必须完成自己声明的核验，不能依赖目标库会话的外键开关或既有表是否严格按迁移创建。请在同一个目标事务内、提交前显式核对三张从表的孤儿引用为零；任何不一致抛出 `MigrationError` 并整批回滚。增加“行数匹配但 FK 被目标侧改变”的回归测试。

### [P2] 旧 source PDF 绝对路径会原样写入中心库

位置：`backend/scripts/migrate_sqlite_to_mysql.py:196-198`

Task 5 的正式持久化边界使用 `sanitize_source_pdf_meta()`，明确禁止保存绝对路径；本脚本只验证 `source_pdf_json` 是 object，随后原样插入。独立探针写入 `C:\\Users\\alice\\secret.pdf`，迁移退出 `0`，目标 `history.source_pdf_json` 仍包含完整 `pdf_path`。

这会把旧电脑用户名和目录结构带入阿里云中心库，也使迁移数据不符合当前 repository 的存储不变量。请复用 `backend/storage/learning_repository.py` 的 `sanitize_source_pdf_meta()`，只保留状态、受控 URL、错误和安全 basename，并补绝对 Windows/POSIX 路径测试。

## 已核查且符合要求的部分

- SQLite 源连接使用 URI `mode=ro`；脚本没有删除或写入源文件。
- 非 dry-run 的 online backup 能包含已提交 WAL；备份在目标写入前创建，命名带 UTC 微秒且不覆盖。
- dry-run 不读取数据库 URL 环境变量、不连接目标库、不创建备份。
- 旧 sessions 仅计数并显示 `excluded`，原始 token 不导入；目标 session 也要求为零。
- 用户默认采用停用 student；只有显式映射可赋 teacher/admin；学生只有映射学号时才允许启用；占位学号和映射学号有重复保护。
- 最早期缺列 fixture 可迁移；无效 JSON/时间、孤儿源引用、缺表和非空目标都会安全失败。
- 用户、history、settings、proof 按依赖顺序在一个 `engine.begin()` 事务中写入；现有计数失败测试证明 SQLite 目标会整批回滚。
- 成功/失败输出没有打印密码哈希、LLM API key、raw session token 或数据库 URL；未知驱动异常只打印异常类型。
- `SQLAlchemy`、`PyMySQL` 和 `cryptography` 已列入 requirements；当前 Core insert、JSON、Boolean 和显式主键写法可由 MySQL 方言使用。
- 目标相关五表任一非空即拒绝导入，不覆盖现有集中式业务数据。

## 独立验证证据

聚焦测试：

```text
.venv\Scripts\python.exe -m pytest tests\test_sqlite_migration.py -q
15 passed in 12.01s
```

Task 1–6 精确回归：

```text
.venv\Scripts\python.exe -m pytest \
  tests\test_database_models.py tests\test_auth_mysql.py \
  tests\test_admin_authorization.py tests\test_admin_user_import.py \
  tests\test_learning_storage.py tests\test_sqlite_migration.py \
  scripts\test_agent_import.py scripts\test_paused_history_resume.py -q
165 passed in 69.33s
```

静态编译：

```text
.venv\Scripts\python.exe -m py_compile \
  scripts\migrate_sqlite_to_mysql.py tests\test_sqlite_migration.py
exit 0
```

SHA-256 与实现报告一致：

```text
migrate_sqlite_to_mysql.py  4471D79DF1A580ECB7E5EEFC84800CB47B20D56B0E8C0CF41E50BC56164C14A7
test_sqlite_migration.py     C47CE6796A4B6EDFA5C3B93328C3083EC1F0B815B49718862AC528780127BBD6
```

OCR 既有失败也独立隔离复现：

```text
.venv\Scripts\python.exe -m pytest tests\test_ocr_engine_protocol.py -q
1 failed in 1.01s
ConnectionAbortedError: [WinError 10053]
```

失败发生在本地测试 HTTP handler 未消费 chunked POST body 时关闭连接的既有路径，与 Task 6 的迁移脚本及测试无调用关系。本审查没有修改 OCR 模块。

## 复审门槛

1. 为上述三个 finding 各补一个先红后绿的回归测试。
2. 非 dry-run 从已创建的 online backup 快照读取待迁移数据。
3. 同一目标事务内核对 counts、sessions 和三张从表的用户外键后才允许提交并退出 `0`。
4. source PDF 元数据经过现有 sanitizer 后再入库。
5. 重新运行 15+新增聚焦测试、Task 1–6 精确回归和 `py_compile`。

