# Task 6 修复轮次 1 报告

## 审查结论处理

独立审查提出的 3 项 finding 已在 mirror 修复；未修改正式源码或 `api_v2.py`。

### 1. 备份与迁移统一使用同一快照

- dry-run 仍直接以 URI `mode=ro` 读取原 SQLite，不创建备份、不读取目标 URL、不连接目标库。
- 非 dry-run 先通过 read-only SQLite online backup 固化恢复快照。
- counts、JSON 解析、源外键校验和目标导入随后只读取该 `.bak` 文件，不再预读原源文件。
- 备份创建完成后，即使源解析、目标约束或目标核验失败，也保留完整备份供恢复。
- 并发边界测试在 `_create_backup` 调用边界向原源提交第 3 个用户，断言 CLI count、备份和目标均为同一快照的 3 行。

### 2. 目标事务提交前显式核对外键

- 在同一个 `engine.begin()` 事务中、计数和 session 核对之后，对 `history`、`user_settings`、`proof_workspaces` 分别执行 `LEFT JOIN users`。
- 任一从表存在无父用户的记录即抛出 `MigrationError`，整批目标写入回滚并返回非零退出码。
- 回归测试使用 SQLite `AFTER INSERT` trigger 将 `history.user_id` 改为 `999`，保持表行数完全一致；脚本现在能识别并回滚所有目标数据。

### 3. source PDF 元数据复用正式清洗边界

- `history.source_pdf_json` 解析后调用 `storage.learning_repository.sanitize_source_pdf_meta()`。
- Windows 和 POSIX 绝对路径只保留安全 basename；未知内部目录字段被丢弃。
- 仅保留正式仓储允许的状态、available、error、受控 URL，以及 `pdf_name`、`source_name`、`log_name`。

## TDD 证据

### RED

在修改生产脚本前运行 4 个定向测试：

```text
4 failed in 2.80s
```

失败分别证明：

- 并发边界下 `backup_users=3`、`target_users=2`；
- 目标 trigger 制造孤儿后脚本仍退出 `0`；
- `pdf_path`、`source_path`、`log_path` 和内部目录原样进入目标 JSON；
- 坏源 JSON 在备份创建前失败，恢复备份数量为 `0`。

### GREEN

同一组定向测试修复后：

```text
4 passed in 2.57s
```

最终 focused：

```text
python -m pytest tests/test_sqlite_migration.py -q
18 passed in 13.91s
```

Task 1–6 精确回归：

```text
python -m pytest \
  tests/test_database_models.py tests/test_auth_mysql.py \
  tests/test_admin_authorization.py tests/test_admin_user_import.py \
  tests/test_learning_storage.py tests/test_sqlite_migration.py \
  scripts/test_agent_import.py scripts/test_paused_history_resume.py -q

168 passed in 66.47s
```

静态编译：

```text
python -m py_compile scripts/migrate_sqlite_to_mysql.py tests/test_sqlite_migration.py
exit 0
```

## SHA-256

- `backend/scripts/migrate_sqlite_to_mysql.py`: `7AB22F50926B337EC05E44FE0D503BE863BF004F7E76EB6A8D78634603FD45F1`
- `backend/tests/test_sqlite_migration.py`: `8C987D69C74F5E2B79C3141523750498EA29B38D56C56D5077428A48C2651360`

## 已知限制

- 本轮继续使用 SQLite SQLAlchemy 目标模拟事务与 trigger 篡改，并复用项目已有 MySQL 方言模型测试；未连接真实阿里云 RDS。
- 迁移期间若原桌面 SQLite 在快照完成后继续产生新写入，新数据不会进入本次目标库；这是快照迁移的预期边界。上线切换时仍应停止旧应用写入，并以最终 dry-run/正式迁移计数进行验收。

