# Task 3 implementation report

## Status

Completed in the writable mirror only. The formal source at
`D:\ywkeji\pdfPipeline-main` was not modified.

Implemented:

- authenticated password change with the 8–128 character policy;
- prompt-state clearing, complete old-session revocation, and one fresh session;
- administrator-only password reset with a cryptographically secure temporary password;
- administrator-only account status changes, session revocation on disable, and self-disable protection;
- route-level role authorization with distinct 401/403 behavior;
- stable malformed-body and unknown-user responses;
- regression coverage proving `initial_password_pending` does not block history access and secrets do not enter application logs.

## Files

Modified:

- `backend/services/auth_service.py`
- `backend/storage/auth_repository.py`
- `backend/api_v2.py`
- `backend/tests/test_auth_mysql.py`

Created:

- `backend/tests/test_admin_authorization.py`
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-3-report.md`

## RED evidence

Tests were added before production changes, then run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
```

Result: exit code 1, `23 failed, 23 passed in 13.29s`. The 23 expected
failures all reached the requested URLs and received the pre-implementation
404 behavior because the password-change and two administrator routes did not
exist. The existing history behavior already satisfied the new
initial-password-prompt regression test.

## GREEN and regression evidence

Focused GREEN command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
```

Result: exit code 0, `46 passed in 15.98s`.

Task 1–3 combined regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

Result: exit code 0, `68 passed, 7 warnings in 17.33s`. All seven warnings
are pre-existing `datetime.utcnow()` deprecation warnings in legacy history/job
paths in `api_v2.py`; Task 3 did not add them.

Syntax verification:

```powershell
.\.venv\Scripts\python.exe -m py_compile services\auth_service.py storage\auth_repository.py api_v2.py tests\test_auth_mysql.py tests\test_admin_authorization.py
```

Result: exit code 0 with no output.

## SHA-256

- `backend/services/auth_service.py`: `126C97BF345667D35D625C0B3DCBA0061536E3041CB6201EDD84304C266E12A3`
- `backend/storage/auth_repository.py`: `0BAB871D97ED32C74EFD17A1DFE877D8980E36ABAC451E7FC97F1F7C6AC22CBF`
- `backend/api_v2.py`: `FC6BA2EA28A7493E35819140CE31732BE5C8A29D60BB6C889F98EFD66FDC97E8`

## Self-review and concerns

- Password replacement and session rotation share one repository transaction;
  account disabling and session revocation also share one transaction.
- Raw bearer tokens and temporary passwords are created only in service-local
  variables and API responses. Persistence receives only SHA-256 token digests
  and Werkzeug password hashes. No new logging was introduced.
- Authorization exists at both the route boundary and service boundary for
  administrator operations.
- Tests use real SQLAlchemy persistence and Flask routes; only the unrelated
  optional `JoinAgent` dependency is replaced during API import.
- No live Alibaba Cloud MySQL connection was available in this task, so the
  verified database runtime is isolated SQLAlchemy SQLite. Live MySQL smoke
  verification remains a deployment-stage concern.
- The new role-aware routes target Web mode. The explicitly preserved desktop
  legacy SQLite authentication model has no role fields and was not expanded in
  Task 3.

## Fix round 1 — serialized authentication state

### Review issue disposition

Resolved the single high-severity TOCTOU issue from `task-3-review.md`.
Login, password change, administrator reset, and account status changes now use
the same per-user database serialization boundary:

- `AuthRepository.user_transaction_by_identifier(...)` and
  `AuthRepository.user_transaction_by_id(...)` open one repository transaction
  and select the target `users` row with `SELECT ... FOR UPDATE`;
- `AuthUserTransaction` performs session validation/insertion/revocation,
  password-state replacement, and active-status changes through that same
  SQLAlchemy `Session` while the user row remains locked;
- `AuthService` performs every current-password, active-status, and current-token
  decision inside that context. It converts the locked ORM entity to the frozen
  `AuthenticatedUser` value before leaving the context, so no detached locked
  `User` is returned or used for a later security decision;
- the former password/status repository write methods that could begin their
  transaction after a lock-free precheck were removed.

This establishes the required ordering on MySQL/InnoDB: a login holding the row
lock commits its session before a waiting password/status transaction revokes
it; a login or second password change obtaining the lock later reads the newly
committed password/status/session state and rejects stale credentials.

### Fix-round RED evidence

After adding the MySQL SQL and controllable fake-transaction concurrency tests,
before production changes:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
```

Result: exit code 1, `4 failed, 46 passed in 14.38s`.

The four expected failures proved:

- both repository transaction entry points were absent, so no MySQL
  `FOR UPDATE` statement could be observed;
- a login that read the old password could insert a live session after reset
  had already revoked all existing sessions;
- two simultaneous password changes could both pass the old-state precheck and
  return success.

The fake transaction explicitly models the ordering expected from a database
row lock; it is not presented as a real SQLite row-lock test. Separately, both
repository selects are compiled with SQLAlchemy's MySQL dialect and asserted to
contain `FOR UPDATE`.

### Fix-round GREEN and regression evidence

Focused GREEN:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
```

Result: exit code 0, `50 passed in 14.01s`.

Task 1–3 combined regression:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

Result: exit code 0, `72 passed, 7 warnings in 17.61s`. The warnings remain the
pre-existing `datetime.utcnow()` deprecations in legacy history/job paths.

Syntax verification:

```powershell
.\.venv\Scripts\python.exe -m py_compile services\auth_service.py storage\auth_repository.py api_v2.py tests\test_auth_mysql.py tests\test_admin_authorization.py
```

Result: exit code 0 with no output.

### Fix-round SHA-256

- `backend/services/auth_service.py`: `BC0CFEEF72D0BD4CF9EB9B09F1AAA05E238C730795EBF32C5E535BC37E0AFA42`
- `backend/storage/auth_repository.py`: `187E7C412C3B93B4C20F56D6B7E6402B998D87ED8ED56677C760E0F330BEED55`
- `backend/api_v2.py`: `FC6BA2EA28A7493E35819140CE31732BE5C8A29D60BB6C889F98EFD66FDC97E8`

### Remaining verification limitation

No live Alibaba Cloud MySQL connection was available in this fix round. The
tests therefore verify MySQL lock SQL generation plus deterministic service
ordering through a fake transaction boundary, while functional regressions run
against isolated SQLAlchemy SQLite. A live MySQL concurrency smoke test remains
part of deployment verification and is not claimed here.
