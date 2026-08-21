# Task 4 fix round 1 independent review

## Verdict

**APPROVED** — 原审查的 3 项问题均已解决，未发现明显回归。

## Finding disposition

### 1. Addressed — existing course 并发导入不会再创建重复 provisional class

- `backend/storage/auth_repository.py:236-248` 在创建任何用户、班级或成员关系前，先以
  `Course.code` 排序并通过 MySQL `SELECT ... FOR UPDATE` 锁定本批次已存在的课程。
  所有课程锁在进入逐行写入循环前取得，并保持在同一个 session/事务中，因此多个相交
  批次没有发现先锁班级、后反向锁课程的次序倒置。
- `backend/storage/auth_repository.py:271-281` 在持有课程锁后重新查询匹配的
  `TeachingClass`，该查询同样带 `FOR UPDATE`。在 MySQL InnoDB `REPEATABLE READ`
  下这是 locking/current read；即使前面的账号冲突查询已经建立一致性快照，等待前一
  事务提交后仍会读取到其新建班级，而不是继续使用旧快照创建第二条记录。
- `classes_by_code` 仅缓存持锁后的查询/创建结果，避免同一批次对同一课程重复创建。
  生产实现没有引入进程锁；`api_v2.py` 中既有的 job 线程锁与此导入路径无关。
- 新课程仍由 `courses.code` 的唯一索引 `ix_courses_code` 裁决并发竞争。并发失败属于
  `SQLAlchemyError`，会退出同一个 session 事务并由仓储转换为稳定导入失败，因此该批
  用户、课程、班级和成员关系一起回滚。高竞争下数据库仍可能选择一个新课程事务作为
  deadlock/duplicate-key 失败方，这是本轮既定的安全回滚策略，不会形成重复课程或班级。
- 未发现 existing course 路径上的重复 class 或明显锁次序回归。

测试真实性说明：

- `test_existing_course_and_class_lookup_compile_to_mysql_for_update` 记录仓储实际发出的
  SQLAlchemy statement，并用 MySQL dialect 编译，确认 Course 和 TeachingClass 两次
  查询都生成 `FOR UPDATE`；它不是数据库联机测试。
- `test_concurrent_imports_reuse_one_provisional_class_for_existing_course` 使用两个真实 Python
  线程和可控共享事务替身，模拟“课程锁等待 -> 前一事务提交 -> 后一事务重新查询”的时序，
  断言最终 1 个班级、2 个成员关系；替身中的锁不是 InnoDB 锁。
- 因没有可用的阿里云 MySQL 测试连接，本轮没有执行真实 InnoDB 并发烟测。代码结论还基于
  MySQL locking read 的标准事务语义；部署阶段仍应执行 live MySQL 并发烟测。

### 2. Addressed — email local-part 点号边界已收紧

- `backend/services/admin_user_service.py:36-40` 将 local-part 定义为一个或多个合法 atom，
  后续只能以单个点分隔另一个非空 atom。因此前导点、尾随点和连续点均不再匹配。
- 字符集合仍保留常见 dot-atom 用法，包括字母数字、`+`、`_`、`-` 等；多段域名继续可用。
- 独立探针确认 `.alice@example.edu`、`alice.@example.edu`、
  `alice..smith@example.edu` 均返回第 2 行 `email` 错误；
  `alice.smith+math@example.edu` 与 `a_b-c@example-domain.edu.cn` 无错误。

### 3. Addressed — quoted multiline CSV 返回记录起始物理行

- `backend/services/admin_user_service.py:43-70` 的 `_PhysicalLineTracker` 记录 DictReader
  实际消费的物理行；每次读取记录前保存已消费行数，读取后在本次消费片段中定位首个非空
  物理行。
- `backend/services/admin_user_service.py:153-174` 对正常记录和 `csv.Error` 均使用该起点，
  不再直接采用记录结束时的 `reader.line_num`。
- 空白物理行由本次消费片段显式跳过，quoted multiline 记录内部的续行不会改变记录起点。
  独立探针确认：表头第 1 行、空白第 2 行、首条记录第 3 行、重复 multiline 记录跨第 4–5
  行时，重复错误报告第 4 行。

## Fresh verification evidence

在 `backend` 目录亲自运行：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py tests/test_admin_authorization.py -q
43 passed in 9.74s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
100 passed, 7 warnings in 22.36s

.\.venv\Scripts\python.exe -m py_compile services/admin_user_service.py storage/auth_repository.py api_v2.py tests/test_admin_user_import.py
exit 0, no output
```

7 条 warning 均来自 `api_v2.py` 既有历史/任务路径中的 `datetime.utcnow()` 弃用提示，
不是本轮修复新增失败。

复核文件 SHA-256 前缀与追加报告一致：

- `services/admin_user_service.py`: `617556DE449A6B...`
- `storage/auth_repository.py`: `B890A82F3CFC11...`
- `api_v2.py`: `A97D5907EB2504...`

## Final count

Addressed: 3  
Open: 0  
Verdict: **APPROVED**
