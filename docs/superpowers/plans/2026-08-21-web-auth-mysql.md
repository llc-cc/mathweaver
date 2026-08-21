# MathWeaver Web Auth and MySQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MathWeaver 改造成使用独立阿里云 MySQL 数据库、支持学号或邮箱登录、管理员导入学生及安全密码管理的集中式网页应用。

**Architecture:** 保留现有 React Router 前端、Flask `/api/v2` 接口和知识图谱流水线，在后端新增 SQLAlchemy 数据访问层、业务服务层及 Alembic 迁移。认证、历史、设置和证明工作区通过仓储接口访问 MySQL；路由只负责 HTTP 输入输出和权限边界。

**Tech Stack:** Python 3.10+、Flask 3、SQLAlchemy 2、Alembic、PyMySQL、Werkzeug、React 19、React Router 7、TypeScript 5、Vitest 4、MySQL 8.0。

**Spec:** `docs/2026-08-21-web-auth-mysql-design.md`

## Global Constraints

- 正式上线形态是浏览器访问网页端，不以 Electron 桌面程序为入口。
- 新功能只写入 `D:\ywkeji\pdfPipeline-main`，不得写入其他 App 工程。
- 使用独立数据库 `mathweaver`，不得读写或迁移 `uniprism_alphatest_user` 中的表。
- 学号必填且唯一；邮箱可选，填写后唯一；登录支持学号或邮箱。
- 初始密码仅提示修改，不阻塞用户进入系统。
- 关闭公开注册；学生账号由管理员导入。
- 密码、原始会话令牌、数据库凭据和模型 API Key 不得进入日志或版本库。
- 新增模块、状态机、异步流程和数据安全分支必须写简洁中文注释，说明原因和边界。
- 保持接口、持久化、业务决策和 UI 渲染分层。
- 后端变更运行相关 Python 单元测试；前端变更运行 Vitest、TypeScript 检查和构建。
- 当前源码快照没有 `.git`，不得擅自初始化仓库；每个任务使用测试结果和文件哈希作为本地检查点。

## File Structure

### New backend files

- `backend/storage/__init__.py`：公开数据库初始化和会话接口。
- `backend/storage/database.py`：读取数据库环境变量，创建 SQLAlchemy Engine 和事务上下文。
- `backend/storage/models.py`：用户、会话、教学关系、历史、设置、证明工作区 ORM 模型。
- `backend/storage/auth_repository.py`：用户与会话持久化。
- `backend/storage/learning_repository.py`：历史、设置与证明工作区持久化。
- `backend/services/auth_service.py`：登录、会话、修改密码和角色判断。
- `backend/services/admin_user_service.py`：CSV 校验、导入和密码重置。
- `backend/migrations/alembic.ini`：迁移配置，不保存连接凭据。
- `backend/migrations/env.py`：从运行环境创建迁移连接。
- `backend/migrations/versions/20260821_01_web_auth_mysql.py`：首个 MySQL 表结构迁移。
- `backend/scripts/migrate_sqlite_to_mysql.py`：旧 SQLite 数据迁移及数量核验。
- `backend/tests/conftest.py`：隔离数据库和 Flask 客户端夹具。
- `backend/tests/test_auth_mysql.py`：登录、会话和密码测试。
- `backend/tests/test_admin_user_import.py`：管理员导入及重置测试。
- `backend/tests/test_learning_storage.py`：历史、设置和证明隔离测试。
- `backend/tests/test_sqlite_migration.py`：旧数据迁移测试。

### Modified backend files

- `backend/api_v2.py`：路由改用服务与仓储；禁用注册；增加密码及管理员接口；补齐任务资源所有权校验。
- `backend/requirements.txt`：增加 SQLAlchemy、Alembic、PyMySQL 和测试依赖。
- `backend/desktop_app.py`：保持现有桌面启动兼容，不复制认证逻辑。
- `.env.example`：增加无敏感值的 MySQL、会话和 CORS 配置示例。

### New frontend files

- `app/auth-model.ts`：认证用户、角色和登录响应类型。
- `app/routes/PasswordChangeModal.tsx`：修改密码和初始密码提示交互。
- `app/routes/AdminUsers.tsx`：管理员 CSV 导入及密码重置页面。
- `app/routes/auth-model.test.ts`：认证响应规范化和提示状态测试。
- `app/routes/AuthModal.test.tsx`：学号/邮箱登录和无注册入口测试。
- `app/routes/PasswordChangeModal.test.tsx`：提示可跳过、修改成功测试。

### Modified frontend and deployment files

- `app/routes/AuthModal.tsx`：邮箱注册弹窗改为统一登录弹窗。
- `app/routes/auth.ts`：持久化完整用户摘要，不再只保存邮箱。
- `app/routes/home.tsx`：接收新认证对象、显示初始密码提示和角色入口。
- `app/routes.ts`：注册 `/admin/users` 页面。
- `package.json`、`package-lock.json`：增加前端组件测试依赖和认证测试脚本。
- `Dockerfile`：构建时使用同源 API，并保留 React Router 服务端运行。
- `backend/Dockerfile`：生产运行 Flask API。
- `deploy/docker-compose.web.yml`：前端、后端和反向代理编排；RDS 只通过环境变量连接。
- `deploy/nginx.mathweaver.conf`：同源代理前端与 `/api/v2`。

---

### Task 1: Database configuration, models, and first migration

**Files:**
- Create: `backend/storage/__init__.py`
- Create: `backend/storage/database.py`
- Create: `backend/storage/models.py`
- Create: `backend/migrations/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/versions/20260821_01_web_auth_mysql.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_database_models.py`
- Modify: `backend/requirements.txt`
- Modify: `.env.example`

**Interfaces:**
- Produces: `configure_database(url: str | None = None) -> None`
- Produces: `session_scope() -> Iterator[Session]`
- Produces: ORM classes `User`, `LoginSession`, `Course`, `TeachingClass`, `ClassMembership`, `History`, `UserSettings`, `ProofWorkspace`, `AuditLog`
- Produces: `User.create_account(role: str, student_no: str | None, email: str | None, display_name: str, password_hash: str) -> User`
- Consumes: environment variable `MATHWEAVER_DATABASE_URL`; production value follows `mysql+pymysql://mathweaver_app:password@rds.internal:3306/mathweaver?charset=utf8mb4`

- [ ] **Step 1: Add pinned database and test dependencies**

Append compatible ranges to `backend/requirements.txt`:

```text
SQLAlchemy>=2.0.36,<3.0
alembic>=1.14.0,<2.0
PyMySQL>=1.1.1,<2.0
cryptography>=44.0.0,<46.0
pytest>=8.3.0,<9.0
```

- [ ] **Step 2: Write failing model tests**

Create tests that configure `sqlite+pysqlite:///:memory:` only for unit isolation, create metadata, and assert:

```python
def test_student_number_and_email_are_unique(db_session):
    db_session.add(User.create_account(
        role="student", student_no="20260001", email="a@example.edu",
        display_name="学生甲", password_hash="test-hash-a",
    ))
    db_session.commit()
    db_session.add(User.create_account(
        role="student", student_no="20260001", email="b@example.edu",
        display_name="学生乙", password_hash="test-hash-b",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()

def test_student_requires_student_number():
    with pytest.raises(ValueError, match="student_no"):
        User.create_account(
            role="student", student_no=None, email="student@example.edu",
            display_name="缺少学号", password_hash="test-hash",
        )
```

- [ ] **Step 3: Run the tests and verify failure**

Run from `backend`:

```powershell
python -m pytest tests/test_database_models.py -q
```

Expected: collection fails because `storage.database` and models do not exist.

- [ ] **Step 4: Implement configuration and models**

`database.py` must reject missing production configuration instead of silently opening SQLite:

```python
def configure_database(url: str | None = None) -> None:
    resolved = url or os.environ.get("MATHWEAVER_DATABASE_URL", "").strip()
    if not resolved:
        raise RuntimeError("MATHWEAVER_DATABASE_URL is required")
    _engine = create_engine(resolved, pool_pre_ping=True, pool_recycle=1800)
    _session_factory.configure(bind=_engine)
```

Use `VARCHAR` for identifiers, UTC timestamps, MySQL `JSON`-compatible SQLAlchemy `JSON`, foreign keys and explicit indexes. Model validation must require `student_no` only when `role == "student"`.

- [ ] **Step 5: Create the Alembic migration**

The revision creates all named tables with `utf8mb4`, unique indexes on `student_no` and `email`, foreign keys, and indexes on `sessions.token_hash`, `sessions.expires_at`, `history.user_id`, and `proof_workspaces.user_id`.

- [ ] **Step 6: Run unit tests and offline migration rendering**

```powershell
python -m pytest tests/test_database_models.py -q
python -m alembic -c migrations/alembic.ini upgrade head --sql
```

Expected: tests pass; generated SQL contains `users`, `sessions`, `history`, `proof_workspaces`, and no reference to `uniprism_alphatest_user`.

- [ ] **Step 7: Record task checkpoint**

```powershell
Get-FileHash storage\database.py,storage\models.py,migrations\versions\20260821_01_web_auth_mysql.py -Algorithm SHA256
```

Save the output in the task execution notes; do not initialize Git automatically.

### Task 2: Authentication repository and secure session service

**Files:**
- Create: `backend/storage/auth_repository.py`
- Create: `backend/services/__init__.py`
- Create: `backend/services/auth_service.py`
- Create: `backend/tests/test_auth_mysql.py`
- Modify: `backend/api_v2.py:241-252`
- Modify: `backend/api_v2.py:378-441`

**Interfaces:**
- Consumes: `session_scope()`, `User`, `LoginSession`, `AuditLog`
- Produces: `AuthService.login(identifier: str, password: str) -> LoginResult`
- Produces: `AuthService.authenticate(raw_token: str) -> AuthenticatedUser | None`
- Produces: `AuthService.logout(raw_token: str) -> None`
- Produces: `AuthenticatedUser(id: int, student_no: str | None, email: str | None, display_name: str, role: str, initial_password_pending: bool)`

- [ ] **Step 1: Write failing authentication tests**

Cover exact behaviors:

```python
def test_login_accepts_student_number(client, student):
    response = client.post("/api/v2/auth/login", json={"identifier": student.student_no, "password": "Init-1234"})
    assert response.status_code == 200
    assert response.json["user"]["role"] == "student"

def test_login_accepts_case_insensitive_email(client, student):
    response = client.post("/api/v2/auth/login", json={"identifier": student.email.upper(), "password": "Init-1234"})
    assert response.status_code == 200

def test_public_registration_is_disabled(client):
    assert client.post("/api/v2/auth/register", json={}).status_code in {404, 405}
```

Also test identical error messages for unknown user and wrong password, inactive account rejection, expiration, token hashing, and logout revocation.

- [ ] **Step 2: Run the tests and verify failure**

```powershell
python -m pytest tests/test_auth_mysql.py -q
```

Expected: failures because login still expects `email`, register is public, and sessions store raw tokens.

- [ ] **Step 3: Implement repository primitives**

Repository methods must be narrow:

```python
find_user_by_identifier(identifier: str) -> User | None
insert_session(user_id: int, token_hash: str, expires_at: datetime) -> None
find_active_session(token_hash: str, now: datetime) -> User | None
revoke_session(token_hash: str, now: datetime) -> None
revoke_all_user_sessions(user_id: int, now: datetime, except_hash: str | None = None) -> int
```

Normalize email with `strip().lower()` and keep student number as trimmed exact text so leading zeroes survive.

- [ ] **Step 4: Implement session security**

Generate 32 random bytes, return URL-safe raw token, and persist only SHA-256:

```python
raw_token = secrets.token_urlsafe(32)
token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
expires_at = utc_now() + timedelta(seconds=session_ttl_seconds)
```

Default `MATHWEAVER_SESSION_TTL_SECONDS` to `604800` seconds. Reject non-positive or non-integer configuration during startup.

- [ ] **Step 5: Replace auth routes and `_current_user`**

`POST /auth/login` accepts `{identifier,password}` and returns:

```json
{
  "token": "raw-session-token",
  "user": {
    "id": 1,
    "student_no": "20260001",
    "email": "student@example.edu",
    "display_name": "张三",
    "role": "student",
    "initial_password_pending": true
  }
}
```

Remove the public registration route rather than leaving a hidden frontend path. Make `/auth/me` return the same `user` shape.

- [ ] **Step 6: Run authentication and regression tests**

```powershell
python -m pytest tests/test_auth_mysql.py scripts/test_agent_import.py -q
```

Update legacy tests to create fixture users through repositories, never re-enable registration for tests.

- [ ] **Step 7: Record task checkpoint**

```powershell
Get-FileHash storage\auth_repository.py,services\auth_service.py,api_v2.py -Algorithm SHA256
```

### Task 3: Password change, administrator reset, and role authorization

**Files:**
- Modify: `backend/services/auth_service.py`
- Modify: `backend/storage/auth_repository.py`
- Modify: `backend/api_v2.py`
- Modify: `backend/tests/test_auth_mysql.py`
- Create: `backend/tests/test_admin_authorization.py`

**Interfaces:**
- Produces: `require_role(*allowed_roles: str)` Flask decorator
- Produces: `AuthService.change_password(user_id: int, current_password: str, new_password: str, current_token: str) -> str`
- Produces: `AuthService.reset_password(actor: AuthenticatedUser, user_id: int) -> str`

- [ ] **Step 1: Write failing password and authorization tests**

Tests must prove:

```python
def test_initial_password_does_not_block_workspace_api(student_client):
    assert student_client.get("/api/v2/history").status_code == 200

def test_change_password_clears_prompt_and_revokes_other_sessions(
    client, student, issue_session, auth_repository, auth_service,
):
    current_token = issue_session(student.id)
    other_token = issue_session(student.id)
    response = client.post(
        "/api/v2/auth/change-password",
        json={"current_password": "Init-1234", "new_password": "Changed-5678"},
        headers={"Authorization": f"Bearer {current_token}"},
    )
    assert response.status_code == 200
    assert response.json["user"]["initial_password_pending"] is False
    assert auth_repository.find_user_by_identifier(student.student_no).initial_password_pending is False
    assert auth_service.authenticate(other_token) is None

def test_student_cannot_reset_another_users_password(student_client, another_student):
    response = student_client.post(
        f"/api/v2/admin/users/{another_student.id}/reset-password"
    )
    assert response.status_code == 403
```

Also verify reset returns the temporary password once, stores only its hash, marks `initial_password_pending=True`, revokes all sessions, and never logs the password.

- [ ] **Step 2: Run tests and verify failure**

```powershell
python -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
```

- [ ] **Step 3: Implement password policy and services**

Use one shared validator:

```python
def validate_new_password(password: str) -> None:
    if len(password) < 8 or len(password) > 128:
        raise PasswordPolicyError("密码长度必须为 8 至 128 位")
```

Do not add speculative composition rules. Use Werkzeug `generate_password_hash` and `check_password_hash`.

- [ ] **Step 4: Add routes**

```text
POST /api/v2/auth/change-password
POST /api/v2/admin/users/<int:user_id>/reset-password
PATCH /api/v2/admin/users/<int:user_id>/status
```

All three require authentication; admin routes additionally require `admin`. Status changes reject disabling the acting administrator's own account.

- [ ] **Step 5: Run tests**

```powershell
python -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
```

- [ ] **Step 6: Record task checkpoint**

```powershell
Get-FileHash services\auth_service.py,storage\auth_repository.py,api_v2.py -Algorithm SHA256
```

### Task 4: Transactional CSV student import and teaching relation foundations

**Files:**
- Create: `backend/services/admin_user_service.py`
- Create: `backend/tests/test_admin_user_import.py`
- Modify: `backend/storage/auth_repository.py`
- Modify: `backend/api_v2.py`

**Interfaces:**
- Produces: `AdminUserService.validate_csv(stream: BinaryIO) -> ImportPreview`
- Produces: `AdminUserService.import_students(stream: BinaryIO, actor: AuthenticatedUser) -> ImportResult`
- Consumes CSV header: `student_no,display_name,email,class_code,initial_password`

- [ ] **Step 1: Write failing import tests**

Test a valid UTF-8 BOM CSV, generated password when omitted, leading-zero student numbers, duplicate rows, existing database conflicts, invalid email, missing required column, non-admin rejection, and complete transaction rollback.

Representative assertion:

```python
def test_conflict_rolls_back_the_entire_csv(admin_client, user_repository):
    response = upload_csv(admin_client, "student_no,display_name\n0001,甲\n0001,乙\n")
    assert response.status_code == 400
    assert user_repository.count_students() == 0
    assert response.json["errors"][0]["line"] == 3
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
python -m pytest tests/test_admin_user_import.py -q
```

- [ ] **Step 3: Implement two-phase validation and one transaction**

Parse with `csv.DictReader`, normalize all rows, collect every validation error, and write nothing if any error exists. When valid, create missing classes and memberships in one transaction.

Return generated passwords only in the immediate response:

```json
{"created": 2, "generated_credentials": [{"student_no": "0001", "initial_password": "V7m3qP9xK2d"}], "errors": []}
```

- [ ] **Step 4: Add administrator import endpoint**

`POST /api/v2/admin/users/import` accepts one multipart field named `file`, limits the upload to 5 MB, requires `.csv`, and uses the `admin` role decorator.

- [ ] **Step 5: Run import and authorization tests**

```powershell
python -m pytest tests/test_admin_user_import.py tests/test_admin_authorization.py -q
```

- [ ] **Step 6: Record task checkpoint**

```powershell
Get-FileHash services\admin_user_service.py,tests\test_admin_user_import.py -Algorithm SHA256
```

### Task 5: Move history, settings, and proof workspaces behind MySQL repositories

**Files:**
- Create: `backend/storage/learning_repository.py`
- Create: `backend/tests/test_learning_storage.py`
- Modify: `backend/api_v2.py:275-287`
- Modify: `backend/api_v2.py:341-498`
- Modify: `backend/api_v2.py:864-1322`
- Modify: `backend/api_v2.py:2716-2814`
- Modify: `backend/api_v2.py:3014-3415`

**Interfaces:**
- Produces: `LearningRepository` methods for settings, history, task progress and proof workspaces
- Consumes: authenticated `user.id`; all reads and writes require explicit owner identity
- Produces: `get_owned_history(user_id: int, history_id: str) -> History | None`
- Produces: `upsert_job_progress(user_id: int, snapshot: JobSnapshot) -> None`

- [ ] **Step 1: Write failing ownership and persistence tests**

Prove that a second user cannot read another user's job status, result, source PDF, compile log, locator, HTML export or artifact export. Prove running, paused, failed and done progress survives a new Flask application instance using the same database.

- [ ] **Step 2: Run tests and verify current vulnerabilities**

```powershell
python -m pytest tests/test_learning_storage.py -q
```

Expected before implementation: unauthorized job status/result/export cases return success, or persistence cases lose in-memory state.

- [ ] **Step 3: Implement learning repository**

Keep JSON serialization in one boundary. Repository methods return domain dictionaries expected by current routes and never expose SQLAlchemy objects after the transaction closes.

- [ ] **Step 4: Replace direct SQLite calls**

Replace `_get_db`, `_DB_PATH` and standalone `sqlite3.connect` usage for settings, history and proof workspaces. Keep filesystem job artifacts under `MATHGRAPH_DATA_DIR`; database rows store safe relative object identifiers, never unrestricted absolute client input.

- [ ] **Step 5: Enforce ownership on every job resource**

Create one helper that loads the job or persisted history for the authenticated owner. Apply it to status, error detail, result, pause, cancel, resume, source PDF, compile log, locator and both export routes. Anonymous processing is disabled in web mode.

- [ ] **Step 6: Run storage and existing pipeline API tests**

```powershell
python -m pytest tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

- [ ] **Step 7: Record task checkpoint**

```powershell
Get-FileHash storage\learning_repository.py,api_v2.py -Algorithm SHA256
```

### Task 6: Migrate existing SQLite data safely

**Files:**
- Create: `backend/scripts/migrate_sqlite_to_mysql.py`
- Create: `backend/tests/test_sqlite_migration.py`

**Interfaces:**
- Produces CLI: `python scripts/migrate_sqlite_to_mysql.py --sqlite <auth.db> --database-url-env MATHWEAVER_DATABASE_URL`
- Produces exit code `0` only when row counts and foreign-key references match

- [ ] **Step 1: Write a failing migration test fixture**

Create a temporary legacy SQLite database containing two email-only users, sessions, history, settings and proof workspaces. Assert migrated users retain password hashes and receive `role="student"` only when a supplied mapping provides a student number; otherwise use `role="teacher"` only when explicitly mapped, and default legacy accounts to inactive `student` pending administrator review.

- [ ] **Step 2: Run the test and verify failure**

```powershell
python -m pytest tests/test_sqlite_migration.py -q
```

- [ ] **Step 3: Implement dry-run, backup, import, and verification**

The script must:

1. open legacy SQLite read-only;
2. create a timestamped copy beside the source before non-dry execution;
3. print table counts during `--dry-run` without writing MySQL;
4. import in dependency order inside a MySQL transaction;
5. preserve primary identifiers where safe;
6. never import legacy raw session tokens; force users to log in again;
7. compare source and destination counts;
8. roll back and exit non-zero on mismatch.

- [ ] **Step 4: Run migration tests**

```powershell
python -m pytest tests/test_sqlite_migration.py -q
```

- [ ] **Step 5: Record task checkpoint**

```powershell
Get-FileHash scripts\migrate_sqlite_to_mysql.py,tests\test_sqlite_migration.py -Algorithm SHA256
```

### Task 7: Update frontend authentication and optional password prompt

**Files:**
- Create: `app/auth-model.ts`
- Create: `app/routes/PasswordChangeModal.tsx`
- Create: `app/routes/auth-model.test.ts`
- Create: `app/routes/AuthModal.test.tsx`
- Create: `app/routes/PasswordChangeModal.test.tsx`
- Modify: `app/routes/AuthModal.tsx`
- Modify: `app/routes/auth.ts`
- Modify: `app/routes/home.tsx:195-198`
- Modify: `app/routes/home.tsx:593-596`
- Modify: `app/routes/home.tsx:2986-3063`
- Modify: `app/routes/home.tsx:3286-3304`
- Modify: `package.json`
- Modify: `package-lock.json`

**Interfaces:**
- Produces TypeScript `AuthenticatedUser` matching backend JSON exactly
- Produces `AuthState { token: string; user: AuthenticatedUser }`
- `AuthModal.onAuth(auth: AuthState): void`
- `PasswordChangeModal` accepts `requiredHint: boolean`, but closing is always allowed

- [ ] **Step 1: Install component test dependencies**

```powershell
npm install --save-dev @testing-library/react@^16.1.0 @testing-library/user-event@^14.6.0 jsdom@^25.0.1
```

Add script:

```json
"test:auth": "vitest run app/routes/auth-model.test.ts app/routes/AuthModal.test.tsx app/routes/PasswordChangeModal.test.tsx"
```

- [ ] **Step 2: Write failing frontend tests**

Test that the login form renders “学号或邮箱”, never renders “注册” or “创建账号”, posts `{identifier,password}`, stores the returned user, and displays a dismissible initial-password notice.

- [ ] **Step 3: Run tests and verify failure**

```powershell
npm run test:auth
```

- [ ] **Step 4: Implement the shared auth model and storage migration**

`loadAuth` accepts the new `mg_auth` JSON key. For one release, detect legacy `mg_token`/`mg_email`, clear them, and require a fresh login because legacy sessions are not migrated.

```ts
export interface AuthenticatedUser {
  id: number;
  student_no: string | null;
  email: string | null;
  display_name: string;
  role: "student" | "teacher" | "admin";
  initial_password_pending: boolean;
}
```

- [ ] **Step 5: Replace AuthModal behavior**

Use one identifier field and one password field, remove mode state and registration calls, and pass `{token,user}` to `onAuth`.

- [ ] **Step 6: Add optional initial-password prompt and password form**

The notice offers “现在修改” and “暂不修改”. Dismissal changes only local UI state. Successful password change updates `user.initial_password_pending` to false and replaces the token if the backend issues a new one.

- [ ] **Step 7: Run frontend tests and checks**

```powershell
npm run test:auth
npm run test:ocr-state
npm run typecheck
npm run build
```

- [ ] **Step 8: Record task checkpoint**

```powershell
Get-FileHash app\auth-model.ts,app\routes\AuthModal.tsx,app\routes\PasswordChangeModal.tsx -Algorithm SHA256
```

### Task 8: Add the minimal administrator user page

**Files:**
- Create: `app/routes/AdminUsers.tsx`
- Create: `app/routes/admin-users.test.tsx`
- Modify: `app/routes.ts`
- Modify: `app/routes/home.tsx`
- Modify: `package.json`

**Interfaces:**
- Route: `/admin/users`
- Consumes: `AuthState` from local auth storage
- Calls: `/api/v2/admin/users/import`, `/api/v2/admin/users/{id}/reset-password`, `/api/v2/admin/users/{id}/status`

- [ ] **Step 1: Write failing admin page tests**

Test that non-admin users are redirected to `/workspace`, admins can upload `.csv`, validation errors display line numbers, generated credentials are shown once with a download button, and reset requires confirmation.

- [ ] **Step 2: Run the test and verify failure**

```powershell
npx vitest run app/routes/admin-users.test.tsx
```

- [ ] **Step 3: Implement the route and page**

Keep fetch calls in focused client helpers inside the module. Never persist generated credentials to localStorage. Build the credentials download from the immediate response using an in-memory `Blob`, then clear it when navigating away.

- [ ] **Step 4: Add the administrator navigation entry**

Render “用户管理” only when `auth.user.role === "admin"`. The backend remains authoritative and returns 403 for forged navigation.

- [ ] **Step 5: Run frontend tests and checks**

```powershell
npm run test:auth
npx vitest run app/routes/admin-users.test.tsx
npm run typecheck
npm run build
```

- [ ] **Step 6: Record task checkpoint**

```powershell
Get-FileHash app\routes\AdminUsers.tsx,app\routes\admin-users.test.tsx,app\routes.ts -Algorithm SHA256
```

### Task 9: Build the complete web deployment surface

**Files:**
- Create: `backend/Dockerfile`
- Create: `deploy/docker-compose.web.yml`
- Create: `deploy/nginx.mathweaver.conf`
- Create: `docs/WEB_DEPLOYMENT.md`
- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Modify: `.env.example`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Public: HTTPS reverse proxy exposes `/` and `/api/v2/*`
- Internal: frontend listens on `3000`, backend listens on `5001`
- External dependency: `MATHWEAVER_DATABASE_URL` connects to RDS database `mathweaver`

- [ ] **Step 1: Add production server dependency and health checks**

Add `gunicorn>=23.0.0,<24.0` for Linux deployment. Compose health checks call frontend `/` and backend `/api/v2/ping`.

- [ ] **Step 2: Make frontend API same-origin in production build**

Set build argument `VITE_API_ORIGIN=__SAME_ORIGIN__`. Do not place RDS or model secrets in frontend build arguments.

- [ ] **Step 3: Create backend image and startup command**

Run schema migration before Gunicorn only through an explicit one-shot compose service, not in every replica. Backend command:

```text
gunicorn --workers 2 --threads 4 --timeout 300 --bind 0.0.0.0:5001 api_v2:app
```

Mount one controlled data volume for job artifacts. Document that horizontal task execution remains limited until a shared job queue is introduced.

- [ ] **Step 4: Configure same-origin Nginx routing**

Proxy `/api/v2/` and `/health` to backend; proxy other routes to the React Router server. Set upload body limit to `110m`, proxy timeout to `360s`, security headers, and no caching for authenticated API responses.

- [ ] **Step 5: Write deployment and rollback instructions**

`WEB_DEPLOYMENT.md` includes RDS database/user creation, security-group rules, `.env` placement, migration dry-run, backup, image build, start, health verification and rollback to the previous image. It must explicitly state that credentials are never committed.

- [ ] **Step 6: Validate deployment configuration without RDS secrets**

```powershell
docker compose -f deploy/docker-compose.web.yml config
docker build -t mathweaver-web:test .
docker build -t mathweaver-backend:test backend
```

Expected: compose renders with placeholder environment references; both images build; no secret values appear in image history or rendered static frontend.

- [ ] **Step 7: Run complete automated verification**

From `backend`:

```powershell
python -m pytest tests -q
```

From project root:

```powershell
npm run test:ocr-state
npm run test:auth
npx vitest run app/routes/admin-users.test.tsx
npm run typecheck
npm run build
```

- [ ] **Step 8: Record final checkpoint and known limitation**

Record SHA-256 hashes for migration, backend image definition, compose file and frontend build manifest. Document that production OCR support depends on the ECS operating system and MinerU runtime; do not claim Linux OCR readiness from the current Windows-only manifest.
