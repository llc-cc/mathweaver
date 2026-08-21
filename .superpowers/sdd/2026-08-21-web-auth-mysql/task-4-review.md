# Task 4 independent review

## Verdict

**CHANGES REQUESTED** — 1 high-severity and 2 medium-severity issues remain open.

## Findings

### High — Concurrent imports can create duplicate provisional teaching classes

- File: `backend/storage/auth_repository.py:236-286`
- Related schema: `backend/storage/models.py:114-130`

The repository performs an unlocked read of existing courses, then a separate
read-before-insert for the termless `TeachingClass`. `teaching_classes` has no
unique constraint for the logical identity `(course_id, name, term)`. If two
administrators concurrently import different students into the same existing
course/class, both transactions can observe no class and both insert a termless
class. Neither insert violates a constraint, so both transactions may commit,
leaving duplicate classes and memberships split between them.

The user/student unique indexes correctly turn concurrent account conflicts
into a rolled-back `400`, but they do not protect this class foundation. The
batch transaction is atomic individually; the shared class reuse invariant is
not concurrency-safe. Serialize class creation using a deterministic lock on
the existing course row (and re-query after acquiring it), or add an equivalent
database-enforced logical uniqueness strategy that also handles `term IS NULL`.
Add a concurrency-focused regression that would fail without that protection.

### Medium — Email validation accepts non-conservative invalid dot-atoms

- File: `backend/services/admin_user_service.py:36-40, 143-144`

The local-part character class allows a dot in any position and any number of
consecutive dots. Direct probes show both `.alice@example.edu` and
`alice..smith@example.edu` produce no validation errors. Those are not valid
unquoted dot-atoms and do not meet the brief's conservative
`local@domain.tld` rule. Tighten the email validator and add boundary tests for
leading, trailing, and consecutive local-part dots (plus valid common forms).

### Medium — Multiline CSV records report the ending physical line, not the row start

- File: `backend/services/admin_user_service.py:121-124, 156-167`

`reader.line_num` is sampled after `DictReader` has consumed the whole record.
For a later duplicate record spanning physical lines 4–5, the service reports
line 5, while the offending row starts on line 4. The brief explicitly requires
physical CSV line numbers with the header on line 1; the returned location
should identify the start of the record. Track the prior physical line count
before reading each record and add a quoted-multiline regression.

## Checks that passed review

- The service reads at most `5 MiB + 1` actual file bytes and rejects oversize
  content before CSV parsing, password hashing, or repository writes; it does
  not trust `Content-Length`.
- The route requires the multipart `file` field, accepts `.csv`
  case-insensitively, and preserves `401`/`403`/structured `400` behavior.
- UTF-8 BOM decoding, accepted/required headers, cell trimming, leading-zero
  student numbers, case-sensitive student numbers under the MySQL binary
  collation, lowercase email normalization, blank-row skipping, length rules,
  and accumulation of ordinary independent row errors are implemented.
- File duplicates and current database conflicts are detected before staging
  inserts. SQLAlchemy failures roll back users, courses, classes, and
  memberships and become a stable structured `400`; concurrent user unique
  conflicts therefore do not leak a `500` or partial batch.
- Generated passwords use `secrets.token_urlsafe`, satisfy the 8–128 policy,
  are returned only for blank password cells, and supplied passwords are not
  echoed. Only Werkzeug hashes cross the repository boundary or persist.
- The service and route both enforce the administrator role. Repository,
  service, and Flask responsibilities remain separated. No Task 5+ behavior
  was introduced.
- Sequential class/course reuse and preservation of an existing class owner
  are correct; the open defect is specifically concurrent creation.
- Tests use real Flask routing and SQLAlchemy transactions. The database
  rollback test injects a real ORM insert failure after staged writes; mocking
  is limited to the unrelated optional `JoinAgent` import.

## Verification evidence

Run from `backend` on 2026-08-21:

```text
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_import.py tests/test_admin_authorization.py -q
35 passed in 9.67s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
92 passed, 7 warnings in 22.31s

.\.venv\Scripts\python.exe -m py_compile services/admin_user_service.py storage/auth_repository.py api_v2.py tests/test_admin_user_import.py
exit 0, no output
```

The seven warnings are the previously reported `datetime.utcnow()`
deprecations in legacy history/job paths and are outside Task 4.

Additional read-only validation probe:

```text
.alice@example.edu        -> []
alice..smith@example.edu  -> []
duplicate multiline row starting at physical line 4 and ending at line 5
                           -> [(5, 'student_no', 'duplicate student number in file')]
```

## Open issue count

- High: 1
- Medium: 2
- Low: 0
- Total: 3
