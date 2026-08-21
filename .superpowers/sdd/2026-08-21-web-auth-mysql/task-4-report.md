# Task 4 implementation report

## Status

Completed in the writable mirror only. The formal source at
`D:\ywkeji\pdfPipeline-main` was not modified.

Implemented:

- administrator-only multipart CSV student import at
  `POST /api/v2/admin/users/import`;
- UTF-8/BOM parsing, 5 MiB limit, accepted-header enforcement, cell trimming,
  length/email/password validation, blank-row handling, and structured errors;
- in-file and existing-database student-number/email conflict detection before
  any write;
- one SQLAlchemy transaction for students, courses, provisional teaching
  classes, and memberships, with complete rollback on database failure;
- generated one-time initial credentials only for blank password cells;
- password hashing before the repository boundary and logging regression
  coverage for supplied/generated passwords, hashes, and bearer tokens.

## Files

Created:

- `backend/services/admin_user_service.py`
- `backend/tests/test_admin_user_import.py`
- `.superpowers/sdd/2026-08-21-web-auth-mysql/task-4-report.md`

Modified:

- `backend/storage/auth_repository.py`
- `backend/api_v2.py`

No fixture changes were required.

## RED evidence

Tests were written before production changes, then run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py tests/test_admin_authorization.py -q
```

Result: exit code 1, `20 failed, 15 passed in 9.14s`. All 20 new import
tests failed for the expected missing-feature reasons: the Flask endpoint
returned 404, and the direct service-boundary test could not import the new
service. All 15 pre-existing administrator authorization tests stayed green.

## GREEN and regression evidence

Focused GREEN command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py tests/test_admin_authorization.py -q
```

Result: exit code 0, `35 passed in 9.82s`.

Task 1–4 combined regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

Result: exit code 0, `92 passed, 7 warnings in 22.33s`. All seven warnings
are pre-existing `datetime.utcnow()` deprecation warnings in legacy history/job
paths in `api_v2.py`; Task 4 did not add them.

Syntax verification:

```powershell
.\.venv\Scripts\python.exe -m py_compile services/admin_user_service.py storage/auth_repository.py api_v2.py tests/test_admin_user_import.py
```

Result: exit code 0 with no output.

## SHA-256

- `backend/services/admin_user_service.py`:
  `85F976AA2A21248C5C0078745CD01D746387B57E9D5CA66F9FF1C0D88E978489`
- `backend/storage/auth_repository.py`:
  `FC36581419C15D2F993B62AED4255356A4A1A11B9CC483F6F973F5F10B775089`
- `backend/api_v2.py`:
  `A97D5907EB25049A99D847FC4B3B8D3F71F785EA455EBBD41FB6F5EF71BF0DC6`

## Self-review and concerns

- CSV validation completes before the repository is called. Repository conflict
  checks complete before staging an insert. A database exception raised after
  membership staging was verified to roll back users, courses, classes, and
  memberships together.
- Student numbers are never numerically converted, so leading zeroes and case
  are preserved. Emails are lowercased before duplicate/conflict checks.
- Plaintext passwords remain request/service-local. The repository receives
  only Werkzeug hashes. Generated values are returned only for rows that did
  not supply a password, and no new logging was introduced.
- Authorization is enforced by both `@require_role("admin")` and the service
  actor check. Tests use real Flask routing and SQLAlchemy persistence; only the
  unrelated optional `JoinAgent` import is replaced.
- No live Alibaba Cloud MySQL connection was available in this task. Functional
  tests therefore use isolated SQLAlchemy SQLite; the live MySQL smoke test
  remains part of deployment verification.

## Provisional-class-owner limitation

The approved CSV does not contain course or teacher assignment fields. For a
nonblank `class_code`, the import therefore reuses or creates
`Course(code=class_code, name=class_code)` and one matching termless
`TeachingClass`. A newly created class uses the importing administrator as its
accountable placeholder `teacher_id`; later imports preserve the existing
owner. A future teacher-assignment workflow must replace this provisional
ownership explicitly and is outside Task 4.

## Fix round 1 — concurrent class reuse and CSV validation boundaries

### Review issue disposition

All three findings from `task-4-review.md` were addressed.

1. **Concurrent provisional class creation:** existing courses are selected in
   deterministic `Course.code` order with MySQL `SELECT ... FOR UPDATE`.
   While those course locks remain held in the same `session_scope`
   transaction, the matching termless `TeachingClass` is re-queried with
   `FOR UPDATE` before any create. The second current read is required because
   earlier conflict queries may already have established an InnoDB
   `REPEATABLE READ` snapshot. Newly inserted courses continue to rely on the
   existing unique `courses.code` index; a concurrent loser rolls back the
   whole batch through the stable database-error path. No process-local lock or
   schema change was introduced.
2. **Email local-part dots:** the validator now accepts one or more legal
   dot-atom segments separated by single dots. Leading, trailing, and repeated
   dots are rejected while common plus, underscore, hyphen, and multi-label
   domain forms remain accepted.
3. **Multiline CSV locations:** a physical-line iterator records exactly which
   input lines each `DictReader` record consumed. Error locations use the first
   nonblank physical line in that consumed segment, so a quoted multiline
   record reports its start rather than its ending line, including when blank
   lines precede it.

### Fix-round RED evidence

The first targeted RED run was executed before the three production fixes:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py -q -k "existing_course_lookup or concurrent_imports or email_ or multiline_duplicate"
```

Result: exit code 1, `6 failed, 2 passed, 20 deselected in 0.35s`.
The failures proved the course query lacked `FOR UPDATE`, two controlled
transactions created two classes for one existing course, all three invalid
local-part dot forms were accepted, and the multiline duplicate reported line
5 instead of its physical start at line 4. The two common valid dot-atoms
already remained accepted.

Final MySQL MVCC self-review added a separate RED assertion requiring the
post-lock class re-query itself to be a current read:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py::test_existing_course_and_class_lookup_compile_to_mysql_for_update -q
```

Result: exit code 1, `1 failed in 0.27s`; the compiled class query lacked
`FOR UPDATE` even though the course query had acquired its row lock.

### Fix-round GREEN and regression evidence

Targeted lock/current-read and controlled transaction-order tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py -q -k "existing_course_and_class_lookup or concurrent_imports"
```

Result: exit code 0, `2 passed, 26 deselected in 0.10s`.

Focused Task 4 GREEN:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py tests/test_admin_authorization.py -q
```

Result: exit code 0, `43 passed in 9.73s`.

Task 1–4 combined regression:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

Result: exit code 0, `100 passed, 7 warnings in 22.42s`. The warnings remain
the pre-existing `datetime.utcnow()` deprecations in legacy history/job paths.

Syntax verification:

```powershell
.\.venv\Scripts\python.exe -m py_compile services/admin_user_service.py storage/auth_repository.py api_v2.py tests/test_admin_user_import.py
```

Result: exit code 0 with no output.

### Fix-round SHA-256

- `backend/services/admin_user_service.py`:
  `617556DE449A6B6F5B7EC4C33FF885B67DADA84259102F3D8E1E15BA77E2811D`
- `backend/storage/auth_repository.py`:
  `B890A82F3CFC112CF1658B28F41E6C229C45FF95CA76C4AD3A26ED7D5A8A93C3`
- `backend/api_v2.py`:
  `A97D5907EB25049A99D847FC4B3B8D3F71F785EA455EBBD41FB6F5EF71BF0DC6`

### Remaining verification limitation

The SQL tests compile the exact repository statements with SQLAlchemy's MySQL
dialect, and the controlled two-transaction test proves the required lock /
commit / re-query order. No live Alibaba Cloud MySQL connection was available,
so a live InnoDB concurrency smoke test remains part of deployment verification.
