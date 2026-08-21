# Task 5 实现报告 — MySQL 学习数据与完整任务归属

## 状态

已完成。Web 模式的用户设置、任务历史/进度、证明工作区全部迁移到 SQLAlchemy `LearningRepository`；显式桌面兼容模式仍可使用旧 SQLite。任务创建、导入、进度事件及完成/失败/暂停状态都会写入所属用户的中央历史记录。

## TDD 证据

### RED

在任何 Task 5 生产代码修改前创建并运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py -q
```

结果：`35 failed, 1 passed`。失败直接暴露以下缺口：

- `storage.learning_repository` 不存在；
- settings 接受错误 JSON 类型与越界索引；
- 匿名 Web 创建任务返回 202、匿名导入返回 201；
- status/result/export 等任务资源没有统一归属保护；
- `_jobs` 清空后状态与结果不可查询；
- Web 学习路由仍依赖 `_get_db`/`sqlite3.connect`；
- PDF 持久化元数据缺少中央清洗边界；
- 创建时持久化失败无法被安全处理。

### GREEN

聚焦测试：

```text
tests/test_learning_storage.py: 36 passed in 17.32s
```

直接相关脚本：

```text
scripts/test_agent_import.py + scripts/test_paused_history_resume.py:
9 passed in 5.28s
```

Task 1–5 完整回归：

```text
tests/test_database_models.py
tests/test_auth_mysql.py
tests/test_admin_authorization.py
tests/test_admin_user_import.py
tests/test_learning_storage.py
scripts/test_agent_import.py
scripts/test_paused_history_resume.py

136 passed in 51.63s
```

匿名创建“无任务、无目录、无数据库记录”补充断言单独复验：`1 passed in 3.12s`。

静态编译：

```powershell
.\.venv\Scripts\python.exe -m py_compile api_v2.py storage\learning_repository.py tests\test_learning_storage.py scripts\test_agent_import.py scripts\test_paused_history_resume.py
```

结果：退出码 `0`。

## 文件

新增：

- `backend/storage/learning_repository.py`
- `backend/tests/test_learning_storage.py`
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-5-route-checklist.md`
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-5-report.md`

修改：

- `backend/api_v2.py`
- `backend/scripts/test_agent_import.py`
- `backend/scripts/test_paused_history_resume.py`

未修改模型和迁移；Task 1 已有字段足以表达本任务约定的 `JobSnapshot`。

## 实现与安全自审

- `LearningRepository` 所有面向客户端的读写都要求显式 `user_id`，返回深拷贝后的普通 dict/list，不泄漏关闭会话后的 ORM 实体。
- `JobSnapshot` 为 frozen dataclass，只包含 `History` 已有字段，不接收 LLM 密钥、运行时对象或客户端绝对路径。
- 同一 job ID 已属于其他用户时，写入返回 `False`；数据库主键同时兜底并发冲突，不转移归属。
- settings 请求验证 JSON object、configs list、非 bool integer active_index 及索引范围；错误稳定返回 400。
- proof workspace 以 `(user_id, graph_id, node_id)` 隔离，graph ID 限制为 1–64 字符。
- Web 创建任务在 worker 启动前持久化 running snapshot；失败返回 500、移除任务及受控工件目录，不会错误设置 `_history_persisted=True`。
- agent import 在返回 201 前立即持久化 done snapshot 并绑定 `_user_id`。
- source PDF JSON 只保留状态、公开 URL、错误和 basename；下载路径只在所属 job 的受控目录下重建。
- Web 工件 ZIP 不接受无所属任务时的客户端 nodes/edges fallback，工件目录固定由 `_persistent_job_dir(job_id)` 派生。
- failing monkeypatch 同时禁止 `_get_db` 与 `sqlite3.connect` 后，Web settings/history/detail/proof 测试仍通过；剩余 SQLite 调用只属于显式 desktop 分支与旧数据库初始化函数。

资源覆盖详见 `task-5-route-checklist.md`。

## SHA-256

- `backend/storage/learning_repository.py`: `9F143277835B570F8597409F91AE63902015B7E3F3EA5EE034861ED4D52747C6`
- `backend/api_v2.py`: `D17453B8427187AAD20C159521D485C7D2C716F86F7FD67874AC0204F9EF1B3F`

## 已知限制与后续关注

- 大 PDF、编译文件和完整 pipeline cache 仍在单机 `MATHGRAPH_DATA_DIR`，没有对象存储与多服务器共享；数据库只保存安全元数据和任务结果 JSON。
- `_jobs` 与 worker runtime 仍是单进程内存状态。持久化 running 记录可以审计/查询，但进程重启后的孤儿 worker 恢复策略不在 Task 5 范围。
- 错误状态可持久化，运行时 traceback/error detail 不写入 `History`，避免扩大敏感信息持久化；重启后错误详情接口可能返回 409。
- 本任务使用 SQLite SQLAlchemy 测试数据库验证事务与权限；真实阿里云 MySQL 连接、迁移执行、备份恢复和部署烟测留待 Task 9。


