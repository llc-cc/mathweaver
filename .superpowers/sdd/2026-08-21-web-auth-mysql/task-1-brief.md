# Task 1 brief — Database configuration, models, and first migration

Read this first: it is the complete Task 1 requirement. Do not read or implement later tasks.

## Context

MathWeaver currently initializes SQLite tables inside `backend/api_v2.py`. This task adds the independent SQLAlchemy persistence foundation used by later authentication and learning-data tasks. Do not migrate existing routes in this task.

## Binding constraints

- Work only in `D:\ywkeji\pdfPipeline-main`.
- Follow `AGENTS.md` and TDD: create tests, run them and observe the expected RED before production code.
- Add concise Chinese comments for configuration, transaction and data-safety boundaries; do not comment trivial assignments.
- Production requires `MATHWEAVER_DATABASE_URL`; never silently fall back to SQLite.
- Unit tests may pass `sqlite+pysqlite:///:memory:` explicitly.
- Database name in production is `mathweaver`; never reference or touch `uniprism_alphatest_user`.
- Do not place secrets in source, tests, logs, migration files or examples.
- The source snapshot has no `.git`; do not initialize Git and do not commit.
- Do not spawn subagents.

## Files

- Create `backend/storage/__init__.py`
- Create `backend/storage/database.py`
- Create `backend/storage/models.py`
- Create `backend/migrations/alembic.ini`
- Create `backend/migrations/env.py`
- Create `backend/migrations/script.py.mako` if Alembic needs it
- Create `backend/migrations/versions/20260821_01_web_auth_mysql.py`
- Create `backend/tests/conftest.py`
- Create `backend/tests/test_database_models.py`
- Modify `backend/requirements.txt`
- Create or modify root `.env.example`

## Required interfaces

```python
configure_database(url: str | None = None) -> None
session_scope() -> Iterator[sqlalchemy.orm.Session]
User.create_account(
    role: str,
    student_no: str | None,
    email: str | None,
    display_name: str,
    password_hash: str,
) -> User
```

ORM classes: `User`, `LoginSession`, `Course`, `TeachingClass`, `ClassMembership`, `History`, `UserSettings`, `ProofWorkspace`, `AuditLog`.

Use a SQLAlchemy 2 declarative base. Required user fields are `id`, nullable unique `student_no`, nullable unique normalized `email`, `display_name`, `role`, `password_hash`, `initial_password_pending`, `is_active`, `created_at`, and `updated_at`. Student creation without a nonblank student number raises `ValueError`. Roles are exactly `student`, `teacher`, and `admin`.

Required session fields include hashed token, owner, expiry, creation, revocation and last-use timestamps. Teaching tables cover courses, teaching classes and memberships. Learning tables preserve the existing `history`, `user_settings`, and `proof_workspaces` meanings from `api_v2.py`. Add an audit table for actor, action, subject, details and timestamp.

Use UTC timestamps, foreign keys, explicit indexes, and SQLAlchemy `JSON` for structured fields. MySQL uses `utf8mb4`. `create_engine` uses `pool_pre_ping=True` and `pool_recycle=1800`. `session_scope` commits on success and rolls back on exceptions.

## Dependencies

Append these exact compatible ranges:

```text
SQLAlchemy>=2.0.36,<3.0
alembic>=1.14.0,<2.0
PyMySQL>=1.1.1,<2.0
cryptography>=44.0.0,<46.0
pytest>=8.3.0,<9.0
```

The root `.env.example` contains only empty/safe examples for `MATHWEAVER_DATABASE_URL`, `MATHWEAVER_SESSION_TTL_SECONDS=604800`, and `MATHWEAVER_ALLOWED_ORIGINS`.

## Required RED tests

- Duplicate non-null student numbers fail.
- Duplicate non-null emails fail.
- Multiple null emails are allowed.
- `User.create_account(role="student", student_no=None, ...)` raises `ValueError` mentioning `student_no`.
- Teacher/admin creation without a student number succeeds when email is present.
- Unknown role raises `ValueError`.
- `session_scope` commits success and rolls back an exception.
- `configure_database()` without a URL/environment raises `RuntimeError` mentioning `MATHWEAVER_DATABASE_URL`.

Run and record RED:

```powershell
python -m pytest tests/test_database_models.py -q
```

Then implement the minimum production code and record GREEN with the same command.

## Migration verification

The Alembic revision creates every ORM table and its constraints. Run:

```powershell
python -m alembic -c migrations/alembic.ini upgrade head --sql
```

The offline SQL must contain the new tables and must not contain `uniprism_alphatest_user`.

## Deliverable report

Write `D:\ywkeji\pdfPipeline-main\.superpowers\sdd\2026-08-21-web-auth-mysql\task-1-report.md` containing:

- status: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`;
- files created/modified;
- exact RED command and why it failed;
- exact GREEN and migration commands with pass/fail counts;
- SHA-256 hashes of `storage/database.py`, `storage/models.py`, and the migration revision;
- self-review findings and remaining concerns.

Return only status, one-line test summary and concerns to the controller.

