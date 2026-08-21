# Task 6 修复轮次 1 独立复审

## 结论

**APPROVED**

上一轮提出的 3 项 finding 均已修复，没有发现新的阻断问题。复审只读取当前 mirror 的实现并运行测试/探针，未修改生产代码或测试代码。

## 逐项核验

### 1. 非 dry-run 的备份与迁移来自同一快照

- `main()` 在非 dry-run 分支先调用 `_create_backup()`，随后只把返回的 `.bak` 传给 `_load_source()`；没有在备份前预读源库，也没有在备份后重新读取原源文件（`migrate_sqlite_to_mysql.py:391-392`）。
- `_create_backup()` 使用源库只读连接和 SQLite online backup，能包含已提交 WAL；备份完成后的解析失败、目标约束失败和目标核验失败都不会删除已完成备份。
- 并发边界回归会在备份调用处向原源库新增第 3 个用户，断言 CLI、备份和目标均为同一快照的 3 行（`test_sqlite_migration.py:477`）。
- 成功路径还断言原 SQLite 文件字节不变；dry-run 断言不创建备份、不连接目标且不需要目标 URL（`test_sqlite_migration.py:168,185`）。

### 2. 目标核验与回滚

- 用户、history、settings 和 proof 依赖顺序写入同一个 `engine.begin()` 事务；计数、session 排除和三张用户从表的孤儿核验均在提交前执行（`migrate_sqlite_to_mysql.py:327-361`）。
- `_orphan_count()` 对每张从表使用 `LEFT OUTER JOIN users ... WHERE users.id IS NULL`。独立用 MySQL dialect 编译三条语句成功，SQL 兼容 MySQL。
- 目标 trigger 在计数不变时篡改 `history.user_id` 的回归现已返回非零，并验证全部目标业务行回滚（`test_sqlite_migration.py:416`）。通用核验函数同样覆盖 `user_settings` 与 `proof_workspaces`。

### 3. source PDF 元数据清洗

- 迁移脚本解析 `source_pdf_json` 后复用正式仓储的 `sanitize_source_pdf_meta()`（`migrate_sqlite_to_mysql.py:197`）。
- Windows 与 POSIX 绝对目录、未知内部目录和原路径字段不会进入中心库；仅保留状态、可用性、错误、公开 URL 以及安全的 `pdf_name`、`source_name`、`log_name`（`test_sqlite_migration.py:513`）。

### 4. 补充安全边界

- 新建备份只作为 `_load_source()` 的只读输入，不会被当成原源文件写回；原源库始终通过 `mode=ro` 打开。
- 未知异常分支只输出异常类型。独立探针注入同时包含数据库 URL、用户名和秘密值的 `RuntimeError`，得到 `migration failed: RuntimeError`，未回显秘密或 URL。
- 备份路径已存在时会拒绝覆盖；备份内部失败只清理本次未完成的目标备份，不会删除或修改源库。

## 独立验证证据

聚焦测试：

```text
.venv\Scripts\python.exe -m pytest tests\test_sqlite_migration.py -q
18 passed in 14.33s
```

Task 1–6 精确回归：

```text
.venv\Scripts\python.exe -m pytest \
  tests\test_database_models.py tests\test_auth_mysql.py \
  tests\test_admin_authorization.py tests\test_admin_user_import.py \
  tests\test_learning_storage.py tests\test_sqlite_migration.py \
  scripts\test_agent_import.py scripts\test_paused_history_resume.py -q
170 passed in 77.73s
```

当前 mirror 的精确集合比修复报告运行时多收集 2 项，全部通过；Task 6 两个文件的 SHA-256 仍与修复报告完全一致。

静态编译：

```text
.venv\Scripts\python.exe -m py_compile \
  scripts\migrate_sqlite_to_mysql.py tests\test_sqlite_migration.py
exit 0
```

SHA-256：

```text
migrate_sqlite_to_mysql.py  7AB22F50926B337EC05E44FE0D503BE863BF004F7E76EB6A8D78634603FD45F1
test_sqlite_migration.py     8C987D69C74F5E2B79C3141523750498EA29B38D56C56D5077428A48C2651360
```

## 已知限制

- 本轮用 SQLite 目标验证事务、trigger 篡改和整批回滚，并用 SQLAlchemy MySQL dialect 编译核对查询；未连接真实阿里云 RDS。
- online backup 之后原桌面应用的新写入不属于该快照，这是快照迁移的预期边界；正式切换仍需停止旧应用写入并核对最终计数。

