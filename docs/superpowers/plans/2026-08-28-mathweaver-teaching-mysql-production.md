# MathWeaver 教学版 MySQL、图谱数据与正式部署实施计划

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan.

**Goal:** 以 `pdfPipeline-teaching-briefing-redesign` 为唯一正式源码，将 Web 持久化从 SQLite 迁移到服务器现有 MySQL，幂等导入凸优化图谱数据，并在不影响现有 3000/5001 服务的前提下完成旁路部署和验收。

**Architecture:** Flask 路由只负责认证、输入校验和响应组装，业务事务进入 Service，所有 Web 持久化通过 SQLAlchemy 2 Repository 访问 MySQL；图谱完整 JSON 继续保存在 `history` 和不可变 `education_snapshots`，学习证据使用关系表。首次发布使用 5002/5174/18080 旁路拓扑，验收通过后再单独决定域名与 HTTPS 切换。

**Tech Stack:** Python 3、Flask、SQLAlchemy 2、PyMySQL、Alembic、MySQL 8.0、pytest、React、TypeScript、Vitest、Vite、Gunicorn、systemd、Nginx。

**Design:** `docs/superpowers/specs/2026-08-28-mathweaver-teaching-mysql-deployment-design.md`

---

## 执行约束

- 开始任何代码任务前重新阅读仓库 `AGENTS.md`；新增模块、状态机、异步流程和数据安全分支写简洁中文注释。
- 不读取、打印或提交服务器 `.env*`、数据库密码、登录令牌和 LLM Key；仓库只提交 `.env.example`。
- Web 模式不允许 SQLite 双写或静默回退；Pipeline 的进程内任务状态不属于本次数据库迁移范围。
- 服务器变更严格按“备份—上传独立版本—迁移—旁路启动—验收—切换”执行；3000 和 5001 在验收期保持不变。
- 每个任务先写失败测试，再写最小实现，运行所列测试后提交；失败时先使用 `superpowers:systematic-debugging`。
- 任何“完成、已通过、已上线”结论前使用 `superpowers:verification-before-completion` 并保留命令输出。

## 阶段一：建立 MySQL 持久化基线

### Task 1：引入数据库依赖、配置和测试夹具

**Files:**

- Modify: `backend/requirements.txt`
- Create: `backend/storage/__init__.py`
- Create: `backend/storage/database.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_database_config.py`
- Create: `.env.example`

**Step 1: 写失败测试**

在 `backend/tests/test_database_config.py` 覆盖以下契约：

- `test_database_url_is_required_in_web_mode`
- `test_create_session_factory_uses_pool_pre_ping`
- `test_session_scope_commits_and_rolls_back`

测试夹具使用 SQLAlchemy SQLite 内存引擎验证 Repository 单元行为，但在 API 回归测试中将 `backend.api_v2.sqlite3.connect` 替换为抛错函数，以证明 Web 请求不会触达旧 SQLite 路径。

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_database_config.py -q`

Expected: 因 `backend.storage.database` 不存在而失败。

**Step 3: 实现最小数据库边界**

`backend/storage/database.py` 提供：

- `build_engine(database_url: str) -> Engine`
- `configure_database(database_url: str | None = None) -> None`
- `get_session_factory() -> sessionmaker[Session]`
- `session_scope() -> Iterator[Session]`（context manager）

MySQL Engine 固定启用 `pool_pre_ping=True`、合理的 `pool_recycle`，Web 模式缺少 `MATHWEAVER_DATABASE_URL` 时立即失败，不回退到本地文件。

`backend/requirements.txt` 增加并固定兼容版本的 `SQLAlchemy`、`PyMySQL`、`alembic`、`gunicorn`；根目录 `.env.example` 只写变量名和占位值。

**Step 4: 运行测试**

Run: `python -m pytest backend/tests/test_database_config.py -q`

Expected: PASS。

**Step 5: 提交**

```bash
git add backend/requirements.txt .env.example backend/storage backend/tests/conftest.py backend/tests/test_database_config.py
git commit -m "feat(storage): establish mysql session boundary"
```

### Task 2：合并基础模型和 Alembic 基线

**Files:**

- Create: `backend/storage/models.py`
- Create: `backend/migrations/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/script.py.mako`
- Create: `backend/migrations/versions/20260821_01_web_auth_mysql.py`
- Create: `backend/migrations/versions/20260821_02_oss_task_prefix.py`
- Create: `backend/tests/test_storage_models.py`
- Create: `backend/tests/test_migrations.py`

**Step 1: 写失败测试**

从 `pdfPipeline-main` 的已验证实现移植基础模型契约，测试至少覆盖：表名、主外键、唯一约束、JSON/Text 字段和删除策略。

```python
BASE_TABLES = {
    "users", "login_sessions", "courses", "teaching_classes",
    "class_memberships", "history", "user_settings",
    "proof_workspaces", "audit_logs",
}

TESTS = {
    "test_base_metadata_contains_server_tables",
    "test_history_keeps_graph_json_and_source_markdown",
    "test_alembic_upgrade_sql_targets_mysql",
}
```

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_storage_models.py backend/tests/test_migrations.py -q`

Expected: 模型或迁移模块缺失。

**Step 3: 移植并校准基础设施**

- 复用旧分支 `backend/storage/models.py` 和前两版迁移，不重新发明已有表。
- 模型以服务器当前 `_02` 迁移状态为基线；迁移 ID 和 `down_revision` 保持原值。
- `history.nodes_json`、`history.edges_json` 用 MySQL JSON，`source_markdown` 用 LONGTEXT/TEXT；不把图谱拆成第二套节点/边真相源。
- Alembic 从 `MATHWEAVER_DATABASE_URL` 取连接串，并在日志中隐藏密码。

**Step 4: 运行测试与迁移 SQL 审计**

Run: `python -m pytest backend/tests/test_storage_models.py backend/tests/test_migrations.py -q`

Run: `python -m alembic -c backend/migrations/alembic.ini upgrade head --sql`

Expected: 测试通过，离线 SQL 可生成且不包含明文密码。

**Step 5: 提交**

```bash
git add backend/storage/models.py backend/migrations backend/tests/test_storage_models.py backend/tests/test_migrations.py
git commit -m "feat(storage): add mysql base models and migrations"
```

### Task 3：定义教学域模型和向前兼容迁移

**Files:**

- Modify: `backend/storage/models.py`
- Create: `backend/migrations/versions/20260828_03_teaching_domain.py`
- Create: `backend/tests/test_teaching_models.py`
- Modify: `backend/tests/test_migrations.py`

**Step 1: 写失败测试**

测试 20 张教学/学习表、索引、唯一约束和外键删除策略。特别验证：

- `test_teaching_class_has_public_id_invite_code_and_archive_state`
- `test_membership_supports_role_student_profile_and_soft_remove`
- `test_snapshot_is_immutable_and_owns_graph_json`
- `test_node_identity_is_unique_within_class`
- `test_occurrence_is_unique_within_snapshot_and_node_number`
- `test_submission_and_evidence_deletes_do_not_cross_students`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_teaching_models.py backend/tests/test_migrations.py -q`

Expected: 教学模型和 `_03` 迁移尚不存在。

**Step 3: 实现模型映射**

基础表兼容策略：

- `teaching_classes` 保留整数主键，新增 `public_id VARCHAR(64) UNIQUE`、`invite_code`、`archived_at`；API 的 `<class_id>` 始终解析为 `public_id`。
- `class_memberships` 保留整数外键，新增 `role`、`student_name`、`student_number`、`removed_at`；教师所有权仍由 `teaching_classes.teacher_id` 决定。
- 教学 UI 创建班级时使用代码为 `mathweaver-general` 的系统课程，Repository 负责幂等创建。
- 将现有 SQLite 教学表映射为 `education_snapshots` 到 `learning_context_summaries` 共 20 张 MySQL 表；JSON、UTC 时间、状态约束和高频组合索引按设计文档实现。
- `_03` 只新增表、列、索引和可空/带默认值约束，不删除或改写服务器已有数据。

**Step 4: 运行测试**

Run: `python -m pytest backend/tests/test_teaching_models.py backend/tests/test_migrations.py -q`

Expected: PASS。

**Step 5: 提交**

```bash
git add backend/storage/models.py backend/migrations/versions/20260828_03_teaching_domain.py backend/tests/test_teaching_models.py backend/tests/test_migrations.py
git commit -m "feat(storage): model teaching and learning domain"
```

## 阶段二：迁移 Web 业务读写

### Task 4：迁移认证、设置、历史和证明工作区

**Files:**

- Create: `backend/storage/auth_repository.py`
- Create: `backend/storage/learning_repository.py`
- Create: `backend/services/__init__.py`
- Create: `backend/services/auth_service.py`
- Modify: `backend/api_v2.py:903-1855`
- Create: `backend/tests/test_auth_mysql.py`
- Create: `backend/tests/test_learning_repository.py`
- Modify: `backend/tests/test_api_v2.py`

**Step 1: 写失败测试**

覆盖注册/登录/退出/当前用户、设置、历史记录、Markdown 下载和证明工作区；验证错误码、跨用户隔离和事务回滚。新增关键测试：

- `test_web_auth_never_calls_sqlite`
- `test_teacher_whitelist_sync_never_downgrades_admin`
- `test_history_graph_round_trip_preserves_nodes_edges_and_markdown`
- `test_user_cannot_read_another_users_proof_workspace`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_auth_mysql.py backend/tests/test_learning_repository.py -q`

Expected: Repository/Service 不存在或 API 仍调用 SQLite。

**Step 3: 实现并替换路由持久化**

- 复用 `pdfPipeline-main` 的 AuthRepository、LearningRepository、AuthService 及密码哈希/令牌摘要策略。
- `sync_teacher_accounts()` 按邮箱将白名单账号同步为 teacher，但绝不降低已有 admin 权限；API 返回兼容字段 `can_teach`。
- `api_v2.py` 的认证、设置、历史和证明工作区路由只调用 Service/Repository，不保留 `_get_db()` 分支。
- 唯一冲突使用 `IntegrityError` 类型映射为 409，不匹配数据库错误字符串。

**Step 4: 运行相关回归**

Run: `python -m pytest backend/tests/test_auth_mysql.py backend/tests/test_learning_repository.py backend/tests/test_api_v2.py -q`

Expected: PASS，且 `forbid_api_sqlite` 未触发。

**Step 5: 提交**

```bash
git add backend/storage/auth_repository.py backend/storage/learning_repository.py backend/services backend/api_v2.py backend/tests
git commit -m "refactor(api): move core web persistence to mysql"
```

### Task 5：迁移班级、成员、图谱快照和作业事务

**Files:**

- Create: `backend/storage/education_repository.py`
- Create: `backend/services/education_access_service.py`
- Modify: `backend/api_v2.py:2666-3418`
- Create: `backend/tests/test_education_repository.py`
- Modify: `backend/tests/test_education_api.py`
- Modify: `backend/tests/test_education_access_control.py`

**Step 1: 写失败测试**

覆盖 `/api/v2/edu/status`、班级 CRUD、加入/移除/恢复成员、快照和作业 CRUD。权限矩阵至少包含：教师所有者、班内学生、已移除学生、其他班教师、未登录用户。

- `test_class_public_id_is_stable_and_internal_id_is_not_exposed`
- `test_join_code_is_unique_and_restores_removed_member`
- `test_snapshot_creation_copies_history_as_immutable_graph`
- `test_teacher_cannot_manage_another_teachers_class`
- `test_assignment_write_rolls_back_when_path_generation_fails`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_education_repository.py backend/tests/test_education_api.py backend/tests/test_education_access_control.py -q`

Expected: 教学路由仍依赖 SQLite。

**Step 3: 实现事务接口**

`EducationRepository` 提供按 `public_id` 查班级、成员生命周期、快照和作业的显式方法；`EducationAccessService` 集中实现所有权/成员资格检查。API 响应继续保持前端现有字段名，内部整数键不暴露。

快照创建在一个事务中复制 `history.nodes_json`、`edges_json`、`source_markdown`，同时建立 node identity/occurrence；任一步失败全部回滚。

**Step 4: 运行测试**

Run: `python -m pytest backend/tests/test_education_repository.py backend/tests/test_education_api.py backend/tests/test_education_access_control.py -q`

Expected: PASS。

**Step 5: 提交**

```bash
git add backend/storage/education_repository.py backend/services/education_access_service.py backend/api_v2.py backend/tests
git commit -m "refactor(education): persist classes snapshots and assignments in mysql"
```

### Task 6：迁移测评、提交、评分和 AI 任务

**Files:**

- Create: `backend/storage/assessment_repository.py`
- Modify: `backend/api_v2.py:3420-4704`
- Create: `backend/tests/test_assessment_repository.py`
- Modify: `backend/tests/test_education_assessment_api.py`
- Modify: `backend/tests/test_education_submission_api.py`

**Step 1: 写失败测试**

覆盖题目编辑/再生成/删除、作业发布和个性化、节点进度、测评尝试、提交、自动评价、人工评分、成绩发布和诊断。重点验证：

- `test_complete_attempt_is_idempotent`
- `test_submit_assignment_creates_one_submission_per_student`
- `test_grade_transaction_updates_questions_submission_and_audit_log`
- `test_ai_usage_is_charged_once_for_same_task_key`
- `test_cross_class_submission_access_is_rejected`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_assessment_repository.py backend/tests/test_education_assessment_api.py backend/tests/test_education_submission_api.py -q`

Expected: API 尚未使用 MySQL Repository。

**Step 3: 实现领域事务**

将 SQLite `ON CONFLICT` 改为 ORM 查询加唯一约束兜底或 MySQL dialect upsert；发布、提交、评分、诊断分别形成清晰事务边界。LLM 调用在事务外执行，结果写入时用任务幂等键避免重复计费和重复题目。

**Step 4: 运行测试**

Run: `python -m pytest backend/tests/test_assessment_repository.py backend/tests/test_education_assessment_api.py backend/tests/test_education_submission_api.py -q`

Expected: PASS。

**Step 5: 提交**

```bash
git add backend/storage/assessment_repository.py backend/api_v2.py backend/tests
git commit -m "refactor(education): persist assessments submissions and grades"
```

### Task 7：迁移学习证据与学生上下文

**Files:**

- Create: `backend/storage/student_context_repository.py`
- Modify: `backend/student_context.py`
- Modify: `backend/api_v2.py:4706-5279`
- Create: `backend/tests/test_student_context_repository.py`
- Modify: `backend/tests/test_student_context.py`
- Modify: `backend/tests/test_education_context_api.py`

**Step 1: 写失败测试**

保留 `student_context.py` 的纯计算测试，新增 Repository 与 API 测试：

- `test_interaction_evidence_and_node_links_commit_atomically`
- `test_feedback_refreshes_only_affected_student_models`
- `test_context_version_changes_after_new_evidence`
- `test_export_contains_only_current_students_data`
- `test_delete_context_does_not_delete_shared_snapshot_identity`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_student_context_repository.py backend/tests/test_student_context.py backend/tests/test_education_context_api.py -q`

Expected: 持久化函数仍接收 `sqlite3.Connection`。

**Step 3: 分离计算与持久化**

- `student_context.py` 保留提示词、模型更新和摘要组装等纯逻辑。
- `StudentContextRepository` 接管 identity/occurrence、interaction、evidence、feedback、student model 和 summary 的查询与写入。
- 证明辅助先读取上下文包，在 LLM 返回后以短事务保存交互和证据；不得在网络调用期间持有数据库事务。
- 导出和删除先通过 `EducationAccessService` 校验班级归属。

**Step 4: 运行测试**

Run: `python -m pytest backend/tests/test_student_context_repository.py backend/tests/test_student_context.py backend/tests/test_education_context_api.py -q`

Expected: PASS。

**Step 5: 提交**

```bash
git add backend/storage/student_context_repository.py backend/student_context.py backend/api_v2.py backend/tests
git commit -m "refactor(learning): move student context persistence to mysql"
```

### Task 8：关闭 Web SQLite 路径并增加就绪检查

**Files:**

- Modify: `backend/api_v2.py`
- Create: `backend/tests/test_web_mode_has_no_sqlite.py`
- Modify: `backend/tests/test_api_v2.py`

**Step 1: 写失败测试**

- `test_all_persistent_web_routes_work_when_sqlite_connect_is_forbidden`
- `test_ready_returns_200_when_mysql_query_succeeds`
- `test_ready_returns_503_without_falling_back_when_mysql_is_down`

AST 测试列出允许保留的非 Web SQLite 兼容代码；`api_v2.py` 的认证、历史、证明和 `/edu` 路由不得出现 `_get_db()` 调用。

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_web_mode_has_no_sqlite.py -q`

Expected: 仍存在遗漏调用或 `/api/v2/ready` 不存在。

**Step 3: 清理遗漏并实现 readiness**

`GET /api/v2/ping` 保持进程存活检查；新增 `GET /api/v2/ready` 执行轻量 `SELECT 1` 并返回数据库状态。数据库异常返回 503 和稳定错误码，不返回连接串或异常堆栈。

**Step 4: 运行完整后端测试**

Run: `python -m pytest backend/tests -q`

Expected: PASS。

**Step 5: 提交**

```bash
git add backend/api_v2.py backend/tests/test_web_mode_has_no_sqlite.py backend/tests/test_api_v2.py
git commit -m "feat(api): enforce mysql-only web persistence and readiness"
```

## 阶段三：固化并导入凸优化图谱

### Task 9：建立图谱数据包、清单和预检器

**Files:**

- Create: `backend/seeds/convex_optimization/bv_cvxbook_1.1-2.3.md`
- Create: `backend/seeds/convex_optimization/node_fixed_round4.json`
- Create: `backend/seeds/convex_optimization/edge_fixed_round1.json`
- Create: `backend/seeds/convex_optimization/manifest.json`
- Create: `backend/services/graph_seed_service.py`
- Create: `backend/tests/test_graph_seed_service.py`

**Step 1: 写失败测试**

- `test_official_dataset_matches_manifest_hashes`
- `test_validator_reports_90_nodes_and_226_edges`
- `test_validator_rejects_duplicate_node_ids`
- `test_validator_rejects_missing_edge_endpoint`
- `test_validator_warns_about_legacy_global_ids_without_mutating_data`

清单固定以下 SHA-256：

- Markdown: `4f0ee13b9410e5751431a10e1607880caaefcd59150d6fa7b0b89d1a27dcd908`
- Nodes: `a0b6a63e86faf0b80af481d4920c4bd9832f4cfe96d669088495385351dc51b6`
- Edges: `3993d48cccf5b9b9cf97b5749e533f4178ffb6301c2abcdda3f91cfa687edf30`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_graph_seed_service.py -q`

Expected: 数据目录和预检器不存在。

**Step 3: 原样复制并实现只读预检**

三份源文件从 `D:\ywkeji\test_result_round1` 原字节复制，禁止格式化 JSON 或修订内容。`validate_graph_dataset()` 返回结构化报告，检查哈希、JSON 结构、节点 ID 唯一性、非空标题/正文、边端点、非空关系理由和计数。

8 个旧版 `global_id` 规则差异、仅 27/90 个节点可映射为连续规范化原文、225/226 条“定义依赖”等已知问题只生成 warning，不自动改写负责人交付数据。

**Step 4: 运行测试和人工哈希复核**

Run: `python -m pytest backend/tests/test_graph_seed_service.py -q`

Run: `Get-FileHash -Algorithm SHA256 backend/seeds/convex_optimization/*`

Expected: 测试通过，三份正式文件哈希与清单一致。

**Step 5: 提交**

```bash
git add backend/seeds/convex_optimization backend/services/graph_seed_service.py backend/tests/test_graph_seed_service.py
git commit -m "data(graph): add validated convex optimization dataset"
```

### Task 10：实现幂等数据库导入命令

**Files:**

- Create: `scripts/import_graph_seed.py`
- Modify: `backend/services/graph_seed_service.py`
- Create: `backend/tests/test_graph_seed_import.py`
- Create: `docs/WEB_DEPLOYMENT.md`

**Step 1: 写失败测试**

- `test_import_creates_history_class_snapshot_and_occurrences_atomically`
- `test_import_is_idempotent_for_same_dataset_teacher_and_class`
- `test_import_rolls_back_everything_on_invalid_occurrence`
- `test_import_records_audit_log_without_secrets`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_graph_seed_import.py -q`

Expected: CLI 和导入事务不存在。

**Step 3: 实现导入事务**

CLI 接口：

```text
python -m scripts.import_graph_seed \
  --dataset backend/seeds/convex_optimization \
  --teacher-email <teacher-email> \
  --class-title "凸优化"
```

- 从环境读取数据库连接，不接受命令行密码。
- 预检通过后，按 dataset key、教师 ID 和班级 public ID 生成确定性 UUID，保证重跑不重复。
- 一个事务创建/复用系统课程、班级、完成态 history、不可变 snapshot、90 个 identity/occurrence 映射及 audit log。
- 导入成功输出不含敏感信息的 JSON 摘要：history/snapshot ID、节点数、边数、warning 数；失败返回非零退出码且不留半成品。

**Step 4: 运行测试**

Run: `python -m pytest backend/tests/test_graph_seed_import.py backend/tests/test_graph_seed_service.py -q`

Expected: PASS，连续执行两次后数据库对象计数不增加。

**Step 5: 提交**

```bash
git add scripts/import_graph_seed.py backend/services/graph_seed_service.py backend/tests/test_graph_seed_import.py docs/WEB_DEPLOYMENT.md
git commit -m "feat(graph): add idempotent mysql seed import"
```

## 阶段四：前端兼容、部署和上线验收

### Task 11：验证教学前端与 MySQL API 契约

**Files:**

- Modify: `src/**`（仅限测试暴露出的兼容问题）
- Modify: `src/**/*.test.ts*`
- Modify: `package.json`

**Step 1: 补充失败测试**

覆盖登录 `can_teach`、班级 public ID、图谱历史/快照、作业状态、学生上下文和 409/503 错误呈现。不得因后端内部键变化修改用户可见路由。

**Step 2: 运行前端基线**

Run: `npx vitest run --config vitest.config.ts`

Run: `npm run build`

Expected: 若契约不兼容则先得到确定失败；若已经兼容，不制造无意义前端改动。

**Step 3: 只修复真实契约差异**

优先保持后端响应兼容；只有教学 UI 必须识别的新稳定错误码或字段才修改前端，并补相应 Vitest。

**Step 4: 再次验证**

Run: `npx vitest run --config vitest.config.ts`

Run: `npm run build`

Expected: PASS。

**Step 5: 提交**

```bash
git add src package.json package-lock.json
git commit -m "test(frontend): verify teaching mysql api contract"
```

如果没有源码差异，只提交新增测试；如果测试已经完整覆盖且无需改动，在执行记录中注明，不创建空提交。

### Task 12：添加可回滚的旁路部署资产

**Files:**

- Create: `deploy/systemd/mathweaver-teaching-backend.service`
- Create: `deploy/systemd/mathweaver-teaching-frontend.service`
- Create: `deploy/nginx/mathweaver-teaching-18080.conf`
- Create: `scripts/build_release.ps1`
- Create: `scripts/deploy_teaching_release.sh`
- Create: `scripts/smoke_teaching_release.sh`
- Modify: `docs/WEB_DEPLOYMENT.md`
- Create: `backend/tests/test_deployment_assets.py`

**Step 1: 写失败测试**

静态测试验证：

- `test_units_bind_only_to_127_0_0_1_sidecar_ports`
- `test_nginx_uses_18080_and_proxies_api_to_5002`
- `test_deploy_script_never_overwrites_3000_or_5001_services`
- `test_release_uses_version_directory_and_atomic_symlink`
- `test_no_secret_literal_exists_in_deploy_assets`

**Step 2: 确认测试失败**

Run: `python -m pytest backend/tests/test_deployment_assets.py -q`

Expected: 部署资产不存在。

**Step 3: 实现部署资产**

- 后端：Gunicorn 绑定 `127.0.0.1:5002`，环境文件位于服务器 `/opt/mathweaver/.env.teaching`，源码不包含密钥。
- 前端：静态站点或受控服务绑定 `127.0.0.1:5174`。
- Nginx：监听 18080，`/api/` 代理到 5002，其余访问 5174；上传大小、超时和安全头显式配置。
- 发布目录：`/opt/mathweaver/releases/<git-sha>`，`/opt/mathweaver/current-teaching` 原子切换；保留最近两版。
- 部署脚本支持 `preflight`、`migrate`、`start`、`rollback`，每步失败立即退出；不自动修改 SSH 或安全组。
- 冒烟脚本检查 ready、登录、图谱历史、班级/快照和静态页面，但不打印 token。

**Step 4: 运行本地验证**

Run: `python -m pytest backend/tests/test_deployment_assets.py -q`

Run: `bash -n scripts/deploy_teaching_release.sh scripts/smoke_teaching_release.sh`

Run: `powershell -NoProfile -File scripts/build_release.ps1 -WhatIf`

Expected: PASS；构建包排除 `.git`、`.env*`、SQLite DB、日志、缓存和任务产物。

**Step 5: 提交**

```bash
git add deploy scripts docs/WEB_DEPLOYMENT.md backend/tests/test_deployment_assets.py
git commit -m "ops(deploy): add reversible teaching sidecar release"
```

### Task 13：发布候选版本到服务器并迁移数据

**Files:**

- No source code changes expected.
- Server targets: `/opt/mathweaver/releases/<git-sha>`, `/opt/mathweaver/backups/`, `/etc/systemd/system/`, `/etc/nginx/conf.d/`

**Step 1: 本地发布门禁**

Run: `git status --short`

Run: `python -m pytest backend/tests -q`

Run: `npx vitest run --config vitest.config.ts`

Run: `npm run build`

Expected: 工作树干净，全部通过。

**Step 2: 服务器只读预检**

确认磁盘至少有本次包、两份代码备份和三份数据库备份所需空间；确认 5002、5174、18080 未占用；确认 MySQL 版本、`alembic_version` 和现有表行数；确认 3000/5001 健康。输出必须脱敏。

**Step 3: 创建可恢复备份**

- 使用权限为 600 的 MySQL client defaults 文件执行 `mysqldump --single-transaction --routines --triggers mathweaver`。
- 归档 `/opt/mathweaver` 当前代码、环境文件和现有 systemd/Nginx 配置；环境文件备份保持 root-only。
- 对 SQL 备份执行非空、gzip 完整性和基本表名检查；未验证备份不得继续。

**Step 4: 上传独立版本并做迁移审计**

- 上传到新的 `<git-sha>` 目录，创建独立 venv，安装锁定依赖，构建前端。
- 运行 `alembic current`、`alembic history` 和离线 SQL 审计；确认服务器由 `_02` 只前进到 `_03`。
- 执行 `alembic upgrade head`，随后验证 20 张新表、基础表新增列、索引和外键。

**Step 5: 启动旁路服务**

- 安装 `mathweaver-teaching-backend.service` 和 `mathweaver-teaching-frontend.service`。
- 启动后先请求 `127.0.0.1:5002/api/v2/ready` 和 `127.0.0.1:5174`。
- 安装 Nginx 18080 配置前执行 `nginx -t`，reload 后从服务器本机和外部各验证一次；若云安全组未放行，只记录为外部访问阻塞，不改写应用。

**Step 6: 导入正式图谱**

以指定教师邮箱和“凸优化”班级运行导入 CLI 两次。第一次应返回 90 nodes/226 edges；第二次应返回同一 history/snapshot ID。SQL 验证 history、snapshot、identity、occurrence 和 audit log 计数。

**Step 7: 生成发布记录**

记录 Git SHA、备份路径、迁移前后 Alembic 版本、服务端口、数据包哈希、导入对象 ID、测试结果和回滚命令；不得记录密码或 token。

### Task 14：执行业务验收、回滚演练和正式切换决策

**Files:**

- Modify: `docs/WEB_DEPLOYMENT.md`（仅补充实际验证结果或环境特例）
- Create: `docs/releases/2026-08-28-teaching-release.md`

**Step 1: 教师链路验收**

登录教师账号，验证图谱历史、90 节点/226 边、Markdown 原文、班级、快照、作业创建/发布、评分和学生上下文摘要。不得用生产密码写自动化脚本。

**Step 2: 学生与权限验收**

新建临时学生，完成邀请码入班、个性化路径、测评、提交和证明辅助。逐项验证未登录、跨用户、跨班级、已移除成员均返回正确拒绝；清理临时验收数据但保留审计记录。

**Step 3: 持久化与故障验收**

重启旁路前后端，确认图谱、作业、学习证据和会话按设计恢复；临时停止旁路后端验证 Nginx 错误页，再恢复服务。不得停止 3000/5001 或生产 MySQL。

**Step 4: 回滚演练**

将 `current-teaching` 切回上一版本并重启旁路服务，验证旧版本健康；再切回候选版本。数据库采用向前兼容 `_03`，应用回滚不执行 destructive downgrade；只有经负责人批准的灾难恢复才使用已验证 SQL 备份。

**Step 5: 安全收口**

在负责人确认有可用 SSH 公钥后，另开 SSH 会话验证密钥登录，再轮换已暴露密码并关闭 root 密码登录；全过程保持一个已验证会话，避免锁死。MySQL 不开放公网。

**Step 6: 正式切换决策**

18080 旁路验收通过不等于完成 HTTPS 正式发布。取得域名/DNS/证书和负责人书面确认后，单独创建 Nginx 域名配置并切换；否则保持受控验收入口，明确记录“等待域名与 DNS”。

**Step 7: 最终全量验证与提交记录**

Run: `python -m pytest backend/tests -q`

Run: `npx vitest run --config vitest.config.ts`

Run: `npm run build`

Run on server: `bash scripts/smoke_teaching_release.sh http://127.0.0.1:18080`

Expected: 全部通过，3000/5001 原服务无回归，数据库备份和版本回滚点可用。

```bash
git add docs/WEB_DEPLOYMENT.md docs/releases/2026-08-28-teaching-release.md
git commit -m "docs(release): record teaching production verification"
```

## 最终完成标准

- 正式源码、Git SHA、数据库迁移、部署版本和图谱数据包哈希可相互追溯。
- Web 认证、设置、历史、证明、教学和学习证据全部由 MySQL 持久化；相关路由不调用 SQLite。
- 凸优化数据原样保存，幂等导入后恰为 90 节点、226 边，并有不可变快照和审计记录。
- 教师/学生全链路、跨用户/跨班权限、重启恢复和回滚演练通过。
- 现有 3000/5001 服务未被覆盖；域名和 HTTPS 未具备时不虚报正式公网切换完成。
- 最终交付说明改了什么、如何验证、数据质量 warning、服务器安全和域名/DNS 等已知限制。
