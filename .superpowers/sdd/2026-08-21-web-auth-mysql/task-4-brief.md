# Task 4 brief — Transactional CSV student import and teaching foundations

Implement only Task 4 in the writable mirror. Do not begin history migration or frontend work.

## Binding rules

- Work only in `D:\dev\Uniprism\uniprism_app\.mathweaver_work\pdfPipeline-main`; follow `AGENTS.md`.
- Strict TDD with recorded RED before production changes. Do not spawn subagents or initialize Git.
- Never log or persist plaintext initial/generated passwords. Persist only Werkzeug hashes.
- Only `admin` may import; authorization remains at the API route and service boundary.
- Validate the complete CSV before any write. If any row/header/database conflict exists, write nothing.
- The valid batch must insert students, any required class foundations, and memberships in one SQLAlchemy transaction. Database errors roll back the whole batch.
- Preserve leading zeroes in student numbers. Student numbers are case-sensitive; emails normalize to lowercase.
- Limit upload to 5 MiB before CSV parsing. Require the multipart field `file` and a `.csv` filename (case-insensitive).

## Existing-schema mapping ruling

The approved Task 1 schema has `Course(code, name)`, `TeachingClass(course_id, teacher_id, name, term)`, and `ClassMembership`, while the approved CSV exposes only `class_code` and no course/teacher columns. To avoid an unapproved schema rewrite:

- blank `class_code`: create only the student;
- nonblank `class_code`: reuse/create `Course(code=class_code, name=class_code)`;
- reuse/create one `TeachingClass` for that course with `name=class_code`, `term=None`;
- when creating that provisional class, set `teacher_id` to the importing administrator as the accountable placeholder owner;
- create `ClassMembership` for the student;
- do not change an existing class owner during later imports.

This establishes relational foundations without inventing a teacher assignment UI. Record this limitation in the report.

## Files

- Create `backend/services/admin_user_service.py`
- Create `backend/tests/test_admin_user_import.py`
- Modify `backend/storage/auth_repository.py`
- Modify `backend/api_v2.py`
- Modify directly affected fixtures only if required.

## Interfaces and data shapes

```python
@dataclass(frozen=True)
class ImportErrorDetail:
    line: int
    field: str
    message: str

@dataclass(frozen=True)
class ImportPreview:
    rows: tuple[NormalizedStudentRow, ...]
    errors: tuple[ImportErrorDetail, ...]

@dataclass(frozen=True)
class ImportResult:
    created: int
    generated_credentials: tuple[GeneratedCredential, ...]
    errors: tuple[ImportErrorDetail, ...]

class AdminUserService:
    def validate_csv(self, stream: BinaryIO) -> ImportPreview: ...
    def import_students(
        self, stream: BinaryIO, actor: AuthenticatedUser
    ) -> ImportResult: ...
```

`AuthRepository` may add a single batch import method that owns the write transaction and returns conflicts in a domain-neutral form. It must not parse CSV or Flask requests.

Accepted header names:

```text
student_no,display_name,email,class_code,initial_password
```

`student_no` and `display_name` columns are required. The other three columns may be absent and behave as blank. Accept UTF-8 with optional BOM. Reject invalid UTF-8 and malformed CSV with structured errors.

## Validation rules

- Trim all cells. Preserve the remaining student-number spelling and leading zeroes.
- `student_no`: required, maximum 64 characters.
- `display_name`: required, maximum 255 characters.
- `email`: optional; if present, lowercase, maximum 255, and must match a conservative `local@domain.tld` form with no whitespace.
- `class_code`: optional, maximum 64 characters.
- `initial_password`: optional; if present use the same 8–128 policy as Task 3.
- Detect every duplicate student number and normalized email in the file. Attach the error to the later row and include its physical CSV line number (header is line 1).
- Detect existing database student-number/email conflicts before writes and return structured errors.
- Ignore completely blank trailing/interior rows. Reject a CSV with no data rows.
- Collect all independently detectable errors, not just the first.
- Never overwrite an existing account.

Generated passwords use `secrets.token_urlsafe` and must satisfy the 8–128 policy. Return only generated values for rows where `initial_password` was blank. Do not echo supplied passwords.

## Endpoint

`POST /api/v2/admin/users/import`

- `@require_role("admin")`.
- Multipart form field `file`.
- Missing auth `401`; authenticated non-admin `403`.
- Missing file/wrong extension/oversize/malformed content/validation conflict: stable `400`, never 500.
- Success `200`:

```json
{
  "created": 2,
  "generated_credentials": [
    {"student_no": "0001", "initial_password": "one-time-value"}
  ],
  "errors": []
}
```

- Validation failure `400` returns `created: 0`, empty `generated_credentials`, and structured `errors` with `line`, `field`, `message`.

## Required RED tests

- Admin can import UTF-8 BOM CSV and leading-zero student numbers.
- Missing initial password generates a returned credential once; supplied password is not echoed.
- Stored password is a hash and verifies; imported users have role `student`, active true, pending true.
- Valid class codes create/reuse course, provisional class, and membership without changing prior owner.
- File duplicate student number and normalized email report later row lines and write zero rows.
- Existing database student/email conflicts write zero rows.
- Invalid email, missing required header, blank required value, overlength field, invalid password, invalid UTF-8, and no data rows return structured 400 errors.
- A simulated database failure after at least one staged insert rolls back users/classes/memberships completely.
- Missing file, wrong extension, and >5 MiB upload return 400.
- Student and teacher receive 403; missing auth receives 401.
- Response/log capture proves plaintext passwords, generated credentials, raw tokens, and hashes are not logged.

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py tests/test_admin_authorization.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

## Report

Write `.superpowers/sdd/2026-08-21-web-auth-mysql/task-4-report.md` with files, RED/GREEN evidence, exact pass counts, hashes for `services/admin_user_service.py`, `storage/auth_repository.py`, `api_v2.py`, self-review, and the provisional-class-owner limitation.
