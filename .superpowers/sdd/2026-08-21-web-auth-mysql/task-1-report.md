# Task 1 report — Database configuration, models, and first migration

- status: `DONE_WITH_CONCERNS`

## Files created or modified

- Created `backend/storage/__init__.py`
- Created `backend/storage/database.py`
- Created `backend/storage/models.py`
- Created `backend/migrations/alembic.ini`
- Created `backend/migrations/env.py`
- Created `backend/migrations/script.py.mako`
- Created `backend/migrations/versions/20260821_01_web_auth_mysql.py`
- Created `backend/tests/conftest.py`
- Created `backend/tests/test_database_models.py`
- Created `backend/pytest.ini`
- Modified `backend/requirements.txt`
- Created `.env.example`

`backend/.venv` was created locally for verification and is not a source artifact.

## TDD record

### RED

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -q
```

Result: failed during collection with `ModuleNotFoundError: No module named 'storage'` from `tests/conftest.py`. This was the expected RED state: the required persistence module did not yet exist. No test assertions ran.

### GREEN

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -q
```

Result: `9 passed in 0.13s` (9 passed, 0 failed).

The tests cover duplicate non-null student numbers and emails, multiple null emails, role and student-number validation, teacher/admin creation, successful transaction commit, rollback after an exception, and the required runtime error when no database URL is supplied.

## Alembic offline verification

Command:

```powershell
.\.venv\Scripts\python.exe -m alembic -c migrations/alembic.ini upgrade head --sql
```

Result: exit code 0. Generated SQL created all 9 ORM tables (`users`, `login_sessions`, `courses`, `teaching_classes`, `class_memberships`, `history`, `user_settings`, `proof_workspaces`, and `audit_logs`) with MySQL `utf8mb4`, indexes, foreign keys, and the user-role constraint. The output contained 0 references to `uniprism_alphatest_user`.

## SHA-256

- `backend/storage/database.py`: `F52D90BDE9031F9F950CBE255BCAD4255E5BCCA4A00AC700EE73B61051A52977`
- `backend/storage/models.py`: `4C31FC4276ADEB27261F5339FD3A20997C309A6E6221E25511247771DB9CF727`
- `backend/migrations/versions/20260821_01_web_auth_mysql.py`: `39AB67D6ACAB5D6D48B00C80A35987540B792ADEFCD227848B4E9A4874160A06`

## Self-review and remaining concerns

- Confirmed runtime database configuration has no SQLite fallback: it requires an explicit argument or `MATHWEAVER_DATABASE_URL`.
- Confirmed `session_scope` commits successful work and rolls back exceptions; the session token model stores only a token hash.
- Confirmed this task does not change existing SQLite-backed routes in `api_v2.py`.
- The migration was verified offline only; no live MySQL server was available for an online migration run.
- This Windows environment's pytest `cacheprovider` hung in its session-finish temporary-cache creation after all assertions had passed. `backend/pytest.ini` disables only that cache plugin so the required unmodified pytest command exits reliably; test caching is unavailable until that environment issue is resolved.

## Fix Round 1

- status: `DONE_WITH_CONCERNS`

### Findings addressed

- Online Alembic migrations now require a nonblank `MATHWEAVER_DATABASE_URL` and raise a message naming it when absent. The Alembic configuration uses `mathweaver.invalid` only as an unauthenticated MySQL dialect placeholder for `upgrade --sql`; it is never used by `run_migrations_online()`.
- Optional email normalization now trims and lowercases supplied values, then converts an empty result to `None` before the unique index is evaluated.
- The rollback test now flushes and observes a database-generated user ID before its deliberate exception, then confirms the row is absent in the next transaction.
- `configure_database()` now constructs a new engine and session factory before swapping globals, preserving the prior configuration when creation fails and disposing the prior engine after a successful swap.

### RED

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -q
```

Result: `3 failed, 9 passed`. The new whitespace-email test failed with `UNIQUE constraint failed: users.email`; the reconfiguration test showed the prior engine was not disposed; and the online Alembic test showed a MySQL localhost authentication attempt instead of an error mentioning `MATHWEAVER_DATABASE_URL`.

### GREEN

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -q
```

Result: `12 passed in 0.87s` (12 passed, 0 failed).

### Offline migration verification

Command:

```powershell
.\.venv\Scripts\python.exe -m alembic -c migrations/alembic.ini upgrade head --sql
```

Result: exit code 0. The generated SQL still creates all 9 ORM tables with `utf8mb4`, constraints, indexes, foreign keys, and 0 references to `uniprism_alphatest_user`.

### Updated SHA-256

- `backend/storage/database.py`: `665C9AD3128C5AE50664F3BB33350FCC9A90FF7222625DF3BE6B0AD2F9E7D7CA`
- `backend/storage/models.py`: `53676936A35B339D6D73CA802DF360FF03207A7725F096B42CF39C4F4AE62BCD`
- `backend/migrations/versions/20260821_01_web_auth_mysql.py`: `39AB67D6ACAB5D6D48B00C80A35987540B792ADEFCD227848B4E9A4874160A06`

### Remaining concern

An environment-gated disposable MySQL smoke test would require container/CI service scope not approved for Task 1. It is deferred to Task 9; Task 1 retains its required SQLite tests and offline MySQL SQL compilation verification.
