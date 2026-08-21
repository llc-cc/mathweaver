# Task 2 brief — Authentication repository and secure session service

Read this first: it is the complete Task 2 requirement. Do not read or implement later tasks.

## Context

Task 1 created SQLAlchemy database/session infrastructure and `User`/`LoginSession` models. This task replaces the web authentication path in `backend/api_v2.py` with a repository and service supporting student number or email, hashed expiring tokens, logout revocation, and disabled public registration. Do not migrate history/settings/proof routes yet.

## Binding constraints and rulings

- Work only in `D:\ywkeji\pdfPipeline-main`; read and follow `AGENTS.md`.
- Follow strict TDD and record RED before production changes.
- Do not spawn subagents. Do not initialize Git.
- Do not log passwords, raw bearer tokens, password hashes or database credentials.
- Production web mode requires `MATHWEAVER_DATABASE_URL`; no SQLite fallback.
- Ruling for desktop compatibility: when `AI4MATH_DESKTOP=1` and no database URL is set, preserve the existing desktop SQLite path for now. This explicit desktop-only compatibility is not a production web fallback. New web auth tests set an explicit SQLAlchemy test URL.
- Disable public registration in web mode by removing the route or returning 404/405. Do not keep a hidden self-registration path.
- Do not implement password change/reset or admin import; those are later tasks.
- Existing legacy tests that created users through `/auth/register` must use a test repository/fixture instead. Never re-enable web registration solely for tests.

## Files

- Create `backend/storage/auth_repository.py`
- Create `backend/services/__init__.py`
- Create `backend/services/auth_service.py`
- Create `backend/tests/test_auth_mysql.py`
- Modify `backend/tests/conftest.py`
- Modify `backend/api_v2.py` only where needed for database startup, `_current_user`, `/auth/register`, `/auth/login`, `/auth/logout`, and `/auth/me`
- Update directly affected existing tests that call `/auth/register`

## Required interfaces

```python
@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    student_no: str | None
    email: str | None
    display_name: str
    role: str
    initial_password_pending: bool

@dataclass(frozen=True)
class LoginResult:
    token: str
    user: AuthenticatedUser

class AuthRepository:
    def find_user_by_identifier(self, identifier: str) -> User | None: ...
    def insert_session(self, user_id: int, token_hash: str, expires_at: datetime) -> None: ...
    def find_active_session(self, token_hash: str, now: datetime) -> User | None: ...
    def revoke_session(self, token_hash: str, now: datetime) -> None: ...
    def revoke_all_user_sessions(
        self, user_id: int, now: datetime, except_hash: str | None = None
    ) -> int: ...

class AuthService:
    def login(self, identifier: str, password: str) -> LoginResult: ...
    def authenticate(self, raw_token: str) -> AuthenticatedUser | None: ...
    def logout(self, raw_token: str) -> None: ...
```

Repository methods may accept a session factory/dependency in their constructor so tests can isolate transactions. They must never own Flask request parsing.

## Identifier rules

- Trim surrounding whitespace from all identifiers.
- If the normalized identifier contains `@`, lookup normalized lowercase `User.email`.
- Otherwise lookup exact `User.student_no`; preserve leading zeroes.
- Empty identifier is invalid.
- Prevent ambiguity when account data is created in later tasks: repository lookup itself must follow the deterministic rule above.
- Unknown account, wrong password and inactive account all return the same client message `学号、邮箱或密码错误` with HTTP 401.

## Session rules

- Parse `MATHWEAVER_SESSION_TTL_SECONDS`, default `604800`.
- Reject non-integer, zero or negative TTL during service construction/startup.
- Generate raw token using `secrets.token_urlsafe(32)`.
- Store only `hashlib.sha256(raw_token.encode("utf-8")).hexdigest()`.
- An active session has no `revoked_at`, has `expires_at > now`, and belongs to an active user.
- Update `last_used_at` after successful authentication.
- Logout hashes the supplied token and revokes the matching row; it is idempotent.
- Use constant-time password verification provided by Werkzeug.

## Required API JSON

`POST /api/v2/auth/login` accepts:

```json
{"identifier": "20260001", "password": "Init-1234"}
```

Successful login returns:

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

`GET /api/v2/auth/me` returns `{"user": <same-user-shape>}`. `POST /api/v2/auth/logout` is idempotent and returns `{"ok": true}`.

## Required RED tests

- Student number login succeeds and preserves leading zeroes.
- Email login is case-insensitive and trims whitespace.
- Login response matches the exact user shape.
- Unknown account and wrong password return the same 401 message.
- Inactive user receives the same 401 message.
- Public registration returns 404 or 405 in web mode.
- Database contains only token hash, never raw token.
- `/auth/me` accepts a valid token and rejects missing/invalid/expired/revoked tokens.
- Logout revokes the current token and is idempotent.
- Session expiration uses the configured TTL.
- Invalid TTL configuration is rejected.

Run RED and later GREEN from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py -q
```

Then run directly affected regression tests. At minimum inspect every `backend/scripts/test_*.py` occurrence of `/auth/register`, update those fixtures, and run their files.

## Deliverable report

Write `D:\ywkeji\pdfPipeline-main\.superpowers\sdd\2026-08-21-web-auth-mysql\task-2-report.md` with:

- status;
- files created/modified;
- exact RED command and expected failure evidence;
- exact GREEN/regression commands and pass counts;
- SHA-256 hashes for `storage/auth_repository.py`, `services/auth_service.py`, and `api_v2.py`;
- self-review and concerns.

Return only status, one-line test summary and concerns.

