# Task 6 实现报告 — SQLite 安全迁移

## 状态

已在 mirror 完成旧 SQLite 到集中式 SQLAlchemy 数据库的迁移脚本。脚本提供：

```text
python scripts/migrate_sqlite_to_mysql.py \
  --sqlite <auth.db> \
  --database-url-env MATHWEAVER_DATABASE_URL \
  [--user-mapping <users.json>] \
  [--dry-run]
```

正式源码和 `api_v2.py` 均未修改。

## 关键行为

- 使用 SQLite URI `mode=ro` 打开源库，源文件永不删除或修改。
- `--dry-run` 不读取目标数据库环境变量、不连接目标库、不创建备份，只校验源数据并打印表计数。
- 非 dry-run 在源文件同目录创建带 UTC 微秒时间戳且不覆盖的 `.bak` 备份。
- 备份使用 SQLite read-only online backup，而不是普通文件复制，因此会包含已提交但尚未 checkpoint 的 WAL 数据。
- 用户、历史、设置、证明工作区按依赖顺序在一个 `engine.begin()` 事务中写入；解析、外键、唯一约束或计数核对失败会整批回滚。
- 旧 `sessions` 只统计并明确标记 excluded，原始 token 永不迁移。
- 目标五张相关表只要已有任何数据即安全失败，重复运行不会覆盖既有记录。
- 保留旧用户主键、密码哈希、创建时间；缺少新增列时使用兼容默认值。
- 映射文件是按规范化 email 索引的 JSON object。只有显式映射才可赋 `teacher`/`admin`；学生具备映射学号才可启用；未映射或缺少学号的学生使用不冲突 `legacy-*` 占位学号并强制停用。
- 日志只输出计数和受控错误，不输出密码哈希、API key、session token 或数据库连接串。

## TDD 证据

### RED

1. 首个 dry-run CLI 测试因脚本不存在失败：退出码 `2`，错误为找不到 `migrate_sqlite_to_mysql.py`。
2. dry-run 最小实现转绿后，真实迁移测试因受控错误 `database migration is not implemented` 失败。
3. WAL 边界测试在普通文件复制实现下失败：源库已有 3 个已提交用户，但备份只包含 2 个；随后改为 SQLite online backup。

### GREEN

聚焦测试：

```text
python -m pytest tests/test_sqlite_migration.py -q
15 passed in 10.92s
```

Task 1–6 精确回归：

```text
tests/test_database_models.py
tests/test_auth_mysql.py
tests/test_admin_authorization.py
tests/test_admin_user_import.py
tests/test_learning_storage.py
tests/test_sqlite_migration.py
scripts/test_agent_import.py
scripts/test_paused_history_resume.py

165 passed in 54.99s
```

静态编译：

```text
python -m py_compile scripts/migrate_sqlite_to_mysql.py tests/test_sqlite_migration.py
exit 0
```

## 测试覆盖

- 成功迁移与最早期缺列旧表兼容；
- dry-run 完全不触碰目标库和备份；
- read-only 源库内容保持不变；
- 普通备份、WAL 一致性备份和不覆盖命名；
- session 排除和敏感日志边界；
- 学生、教师、管理员角色映射；
- 未映射和无学号学生强制停用；
- 坏映射 JSON、重复学号、坏业务 JSON；
- 孤儿外键、目标计数不一致的整批回滚；
- 重复运行和非空目标安全失败；
- 缺少源表和目标环境变量。

## 全仓测试说明

额外运行 `python -m pytest tests -q` 得到 `175 passed, 1 failed`。唯一失败是既有
`tests/test_ocr_engine_protocol.py`：其本地 `BaseHTTPRequestHandler` 未读取流式
chunked POST body 就关闭连接，Windows 稳定返回 `10053 ConnectionAbortedError`；隔离复跑仍可重现。
该模块与 Task 6 两个文件没有调用或修改关系，因此本任务未越界修改 OCR 代码。

## SHA-256

- `backend/scripts/migrate_sqlite_to_mysql.py`: `4471D79DF1A580ECB7E5EEFC84800CB47B20D56B0E8C0CF41E50BC56164C14A7`
- `backend/tests/test_sqlite_migration.py`: `C47CE6796A4B6EDFA5C3B93328C3083EC1F0B815B49718862AC528780127BBD6`

## 已知限制

- 当前验证使用 SQLite SQLAlchemy 目标，且复用 `test_database_models.py` 的 MySQL 方言模型/迁移校验；没有使用真实阿里云 RDS 凭据做在线迁移烟测。
- 迁移要求目标数据库已先通过 Alembic 建表，且相关目标表为空；这是防止覆盖既有集中式业务数据的保守策略。
- 用户映射文件可能包含个人信息，应放在受控运维目录，迁移完成后按学校数据管理要求安全保管或销毁，不应提交版本库。

