# Task 1 review

- **Spec: FAIL** — required models, dependencies, safe example configuration, and offline DDL are otherwise present and consistent, but the online migration path does not enforce the required production environment variable.
- **Quality: NEEDS_CHANGES** — ORM/migration parity and MySQL DDL are sound on inspection, all 9 tests pass, and `pytest.ini` is a narrowly justified workaround; the items below should be corrected before acceptance.

## Actionable findings

1. **HIGH — Spec / data safety:** `backend/migrations/env.py:20,36` falls back to `backend/migrations/alembic.ini:4` for both offline and online migrations. Consequently, `alembic upgrade head` without `MATHWEAVER_DATABASE_URL` attempts `mysql+pymysql://localhost/mathweaver` instead of failing, contrary to the binding production requirement. Keep the safe placeholder only for `upgrade --sql`; require a nonblank environment value and raise an error mentioning `MATHWEAVER_DATABASE_URL` in `run_migrations_online()`.

2. **MEDIUM — Data normalization:** `backend/storage/models.py:49` turns an all-whitespace email into the non-null value `""`. That creates a unique blank-email sentinel rather than the intended nullable normalized email, so a second blank email can fail uniqueness and an account can retain an unusable identifier. Normalize first and return `normalized or None`; add mixed-case/outer-whitespace and blank-email tests.

3. **MEDIUM — Test quality:** `backend/tests/test_database_models.py:82-89` raises immediately after `session.add()`, before a flush, so no SQL write has occurred and the test does not demonstrate that the exception path invokes rollback. Flush before raising and/or spy on `Session.rollback()` so removing the explicit rollback would make the test fail.

4. **LOW — Resource lifecycle:** `backend/storage/database.py:25-30` replaces global engine/factory references without disposing the prior engine; repeated configuration (already done once per test) can leave pools/connections for garbage collection. Construct the new engine/factory first, atomically swap them, then dispose the previous engine; also ensure a failed reconfiguration cannot leave ambiguous stale state.

5. **LOW — MySQL verification gap:** current coverage is SQLite plus offline MySQL compilation. Add a CI smoke test against a disposable MySQL instance that runs `upgrade head` and exercises nullable unique emails, foreign keys, JSON values, and UTC datetime round-trips. This is not a migration-parity defect found in the current files, but it is the remaining compatibility risk noted by the implementer.
