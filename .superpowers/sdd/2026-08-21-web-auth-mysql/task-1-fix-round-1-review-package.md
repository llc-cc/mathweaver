# Task 1 fix round 1 review package

## Scope

Re-review only the four findings in `task-1-review.md` against the current files and the appended Fix Round 1 evidence in `task-1-report.md`.

## Inputs

- Original brief: `D:\ywkeji\pdfPipeline-main\.superpowers\sdd\2026-08-21-web-auth-mysql\task-1-brief.md`
- Original review: `D:\ywkeji\pdfPipeline-main\.superpowers\sdd\2026-08-21-web-auth-mysql\task-1-review.md`
- Updated report: `D:\ywkeji\pdfPipeline-main\.superpowers\sdd\2026-08-21-web-auth-mysql\task-1-report.md`

## Fix files

- `D:\ywkeji\pdfPipeline-main\backend\storage\database.py`
- `D:\ywkeji\pdfPipeline-main\backend\storage\models.py`
- `D:\ywkeji\pdfPipeline-main\backend\migrations\env.py`
- `D:\ywkeji\pdfPipeline-main\backend\tests\test_database_models.py`

## Claimed evidence

- Three new/strengthened tests failed before fixes for the intended reasons.
- Current model suite: 12 passed.
- Alembic offline SQL exits 0.
- Live disposable MySQL remains deferred to the deployment task because Task 1 requires offline verification and no MySQL credential/container is available.

## Re-review contract

- Verify online and offline Alembic never use a localhost/default database URL and fail safely without `MATHWEAVER_DATABASE_URL`.
- Verify whitespace-only optional email becomes `None`.
- Verify rollback test flushes a real write before an exception and proves it was not committed.
- Verify database reconfiguration disposes the previous engine safely.
- Do not introduce new unrelated findings unless the fix itself broke something.
- Return whether every scoped finding is addressed and whether Task 1 is approved.

