# Task 3 brief — Password change, administrator reset, and role authorization

Read this first. Implement only Task 3; do not begin CSV import, frontend, or history migration.

## Context and binding rulings

- Work only in `D:\dev\Uniprism\uniprism_app\.mathweaver_work\pdfPipeline-main` and follow `AGENTS.md`.
- Use strict TDD: write and run failing tests before production changes; record RED and GREEN.
- Do not spawn subagents and do not initialize Git.
- Never log passwords, temporary passwords, raw bearer tokens, hashes, or database credentials.
- `initial_password_pending` is display-only. It must never block `/api/v2/history`, workspace, or any normal authenticated API.
- Password length is 8–128 characters inclusive. Do not invent character-composition rules.
- Password changes verify the current password, set `initial_password_pending=False`, revoke every old session, issue one fresh session token, and return that fresh token with the updated exact user shape. The old current token and other tokens must all become invalid.
- Administrator reset generates a cryptographically secure temporary password, stores only a Werkzeug hash, sets `initial_password_pending=True`, revokes every session, and returns the temporary password only in that response.
- Account status changes require admin. Reject an administrator disabling their own account. Disabling another user revokes all that user's sessions.
- Route-level authorization is mandatory even when service methods also defend their invariants.

## Files

- Modify `backend/services/auth_service.py`
- Modify `backend/storage/auth_repository.py`
- Modify `backend/api_v2.py`
- Modify `backend/tests/test_auth_mysql.py`
- Create `backend/tests/test_admin_authorization.py`
- Modify other directly affected test fixtures only when necessary for isolation.

## Required interfaces

```python
def require_role(*allowed_roles: str): ...

class AuthService:
    def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        current_token: str,
    ) -> LoginResult: ...

    def reset_password(
        self, actor: AuthenticatedUser, user_id: int
    ) -> str: ...
```

The approved plan annotated `change_password(...) -> str`; this brief resolves the API boundary by returning `LoginResult`, because the endpoint must provide both the freshly issued token and updated user state without querying route-local persistence. Do not return or retain the old token.

Repository additions should provide narrow persistence operations such as lookup by ID, updating password/prompt state, changing active status, and session revocation. Repository code must not parse Flask requests.

## Routes and contracts

### `POST /api/v2/auth/change-password`

Requires Bearer authentication. JSON object:

```json
{"current_password":"Init-1234","new_password":"Changed-5678"}
```

Success `200`:

```json
{"token":"fresh-token","user":{"id":1,"student_no":"0001","email":null,"display_name":"张三","role":"student","initial_password_pending":false}}
```

- Missing/invalid bearer: `401`.
- Non-object JSON or non-string fields: stable `400`.
- Wrong current password: stable `400` with no credential detail leakage.
- Policy failure: `400` with `密码长度必须为 8 至 128 位`.

### `POST /api/v2/admin/users/<int:user_id>/reset-password`

Requires admin. Success `200`:

```json
{"temporary_password":"one-time-value"}
```

- Missing auth: `401`; non-admin: `403`; unknown user: `404`.
- The temporary password appears nowhere else in persisted state or logs.

### `PATCH /api/v2/admin/users/<int:user_id>/status`

Requires admin. JSON object exactly containing boolean `is_active` (additional keys may be ignored). Success `200` returns updated exact user shape under `user`.

- Missing auth: `401`; non-admin: `403`; malformed/non-boolean body: `400`; unknown user: `404`.
- Reject self-disable with `400`; self-enable is harmless.

## Required RED tests

- Initial-password flag does not block `GET /api/v2/history`.
- Change password rejects wrong current password and invalid lengths.
- Successful change clears prompt, stores only a hash, revokes the old current token and all other sessions, returns a fresh usable token, and old password no longer logs in.
- Student/teacher cannot call either admin endpoint.
- Missing authentication is 401 rather than 403.
- Admin reset returns a policy-valid temporary password once, stores only its hash, marks pending true, and revokes all sessions.
- Resetting/status-changing unknown user returns 404.
- Status disabling revokes all sessions.
- Acting administrator cannot disable self.
- Malformed JSON/type errors are stable 400, never 500.
- Capture application logs in reset tests and prove no temporary password/hash/raw token is logged.

Run RED and GREEN from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py tests/test_admin_authorization.py -q
```

Then run the Task 1–3 combined regression:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

## Deliverable report

Write `.superpowers/sdd/2026-08-21-web-auth-mysql/task-3-report.md` with status, files, RED evidence, exact GREEN/regression commands and counts, SHA-256 for `services/auth_service.py`, `storage/auth_repository.py`, and `api_v2.py`, plus self-review/concerns. Return only status, test summary, and concerns.
