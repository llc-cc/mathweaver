# Task 1 fix round 1 re-review

- Scoped findings addressed: **4**
- Scoped findings open: **0**
- Approval: **APPROVED**

## Scope and evidence

This review rechecked only findings 1--4 from `task-1-review.md` and direct regressions from their fixes. The deferred disposable-MySQL smoke test is outside this fix-round scope.

## Findings

1. **Alembic database-URL safety — addressed.** `run_migrations_online()` reads only a stripped `MATHWEAVER_DATABASE_URL` and raises `RuntimeError` naming that variable when it is absent or blank. The offline path uses the explicit, non-routable `mysql+pymysql://mathweaver.invalid/mathweaver` dialect placeholder from `alembic.ini`; it does not resolve to a localhost/default database or create an online connection.
2. **Whitespace optional email normalization — addressed.** `User.normalize_email()` trims and lowercases a supplied value, then returns `None` for the empty result. The test covers a mixed-case, outer-whitespace email plus two whitespace-only emails in the same unique index.
3. **Rollback-test evidence — addressed.** The exception-path test calls `session.flush()`, verifies the write received a database-generated ID, raises deliberately, then opens a new transaction and proves the row is absent. This demonstrates rollback of an actual flushed write.
4. **Reconfiguration resource lifecycle — addressed.** `configure_database()` constructs the replacement engine and factory before replacing globals. On construction failure, the prior engine remains active and undisposed; after a successful swap, the prior engine is disposed. The test covers both paths.

## Verification

- `D:\\ywkeji\\pdfPipeline-main\\backend\\.venv\\Scripts\\python.exe -m pytest tests/test_database_models.py -q`: **12 passed in 0.87s**.
- `D:\\ywkeji\\pdfPipeline-main\\backend\\.venv\\Scripts\\python.exe -m alembic -c migrations/alembic.ini upgrade head --sql`: **exit 0**; output contains all nine tables, MySQL `utf8mb4` declarations, and no `uniprism_alphatest_user` reference.

## Direct-regression assessment

No direct regression from the four fixes was found. Live disposable-MySQL validation remains intentionally deferred and is not an open finding for this scoped re-review.
