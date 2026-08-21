# SDD ledger — plan: D:\ywkeji\pdfPipeline-main\docs\superpowers\plans\2026-08-21-web-auth-mysql.md

## Setup

- Spec: `D:\ywkeji\pdfPipeline-main\docs\2026-08-21-web-auth-mysql-design.md`
- Workspace ruling: source snapshot has no `.git`; a worktree and commits are impossible. Work in place because the user explicitly ordered immediate development. Use tests and SHA-256 checkpoints instead.
- TDD ruling: every production behavior change starts with a failing test and records RED/GREEN evidence in the task report.
- Security ruling: no real RDS credential is required during development; unit tests use isolated SQLite through the same SQLAlchemy layer, and MySQL integration remains opt-in via environment variable.

## Pre-flight interface scan

| Tasks | Producer → consumer | Finding / ruling |
| --- | --- | --- |
| 1 → 2 | Database session and `User`/`LoginSession` models → auth repository | Compatible. Task 2 must not recreate engine/session logic. |
| 1 → 4 | Teaching models → CSV import | Compatible. Import uses Task 1 constraints and one transaction. |
| 1 → 5 | History/settings/proof models → learning repository | Compatible. JSON conversion stays in Task 5 repository. |
| 1 → 6 | ORM models/migrations → legacy importer | Compatible. Task 6 imports through repositories/session boundary. |
| 1 → 9 | Environment variables and migrations → deployment | Compatible. Deployment runs Alembic explicitly. |
| 2 → 3 | `AuthService` and `AuthenticatedUser` → password/role routes | Compatible. Task 3 extends the service, not route-local logic. |
| 2 → 5 | Authenticated owner → learning access checks | Compatible. Anonymous web-mode data access is disabled. |
| 2 → 7 | Login JSON shape → frontend auth model | Compatible. Exact `user` properties match the spec. |
| 2/3 → 8 | Role enforcement and admin endpoints → admin UI | Compatible. Backend remains authoritative. |
| 4 → 8 | CSV import response → credential download | Compatible. Credentials are response-only and never persisted client-side. |
| 5 → 6 | Learning repository → migration verification | Compatible. Count verification includes all three learning tables. |
| 5 → 9 | Persistent database state and local artifacts → web containers | Compatible with one controlled artifact volume; horizontal workers remain documented limitation. |
| 7 → 8 | `AuthState` → admin route | Compatible. Admin page consumes shared model. |

## Per-task self-consistency scan

| Task | Tests vs implementation | Files/interfaces | Ruling |
| --- | --- | --- | --- |
| 1 | RED covers uniqueness and student-number rule | Models and migration share metadata | Proceed. |
| 2 | RED covers both identifiers, disabled registration, expiration | Repository methods support service | Proceed. |
| 3 | RED covers optional prompt, password change, revocation, roles | Extends Task 2 interfaces | Proceed. |
| 4 | RED covers validation and transaction rollback | Uses Task 1 teaching models | Proceed. |
| 5 | RED covers persistence and every owner-sensitive resource | Large `api_v2.py` change is isolated behind repository | Proceed, require strict task review. |
| 6 | RED covers legacy data and rollback | Source sessions intentionally not migrated | Proceed. |
| 7 | RED covers new copy, request shape and optional prompt | Auth model matches Task 2 JSON | Proceed. |
| 8 | RED covers role routing, import and one-time credentials | Consumes Tasks 3/4/7 | Proceed. |
| 9 | Verification covers compose, images, backend and frontend tests | Server OS affects OCR only | Proceed; document Windows OCR limitation. |

## Execution log

- Task 1: fix round 1/5 — 4 findings addressed, 0 open. Live disposable MySQL smoke test deferred to Task 9 because Task 1's approved acceptance criterion is offline Alembic verification and no live test database is available.
- Task 1: complete — RED observed, GREEN `12 passed`, Alembic offline SQL exit 0, scoped re-review approved.
- Task 2: fix round 1/5 — 3 findings addressed, 0 open: combined-test database isolation, malformed-login 4xx validation, and MySQL case-sensitive student numbers.
- Task 2: complete — RED observed, combined GREEN `44 passed, 7 warnings` twice, `py_compile` exit 0, scoped re-review approved.
- Task 3: fix round 1/5 — 1 high-severity concurrency finding addressed, 0 open: login/password/status operations now serialize on the MySQL user row with `SELECT ... FOR UPDATE`.
- Task 3: complete — RED observed, focused GREEN `50 passed`, combined GREEN `72 passed, 7 warnings`, `py_compile` exit 0, scoped re-review approved.
- Task 4: fix round 1/5 — 3 findings addressed, 0 open: concurrent provisional-class reuse, conservative email dot-atoms, and multiline CSV start-line reporting.
- Task 4: complete — RED observed, focused GREEN `43 passed`, combined GREEN `100 passed, 7 warnings`, `py_compile` exit 0, scoped re-review approved.
- Task 5: fix rounds 1–4/5 — async persistence failure, restart artifact export, desktop fallback validation, and Windows path/junction isolation findings addressed; 0 open.
- Task 5: complete — RED observed, focused GREEN `53 passed`, related GREEN `62 passed`, Task 1–5 GREEN `153 passed`, `py_compile` exit 0, final scoped re-review approved.
- Task 6: fix round 1/5 — backup/import snapshot consistency, explicit destination FK verification, and source-PDF path sanitization findings addressed; 0 open.
- Task 6: complete — RED observed, focused GREEN `18 passed`, Task 1–6 GREEN `170 passed` at review checkpoint, MySQL dialect FK SQL compile and `py_compile` exit 0, scoped re-review approved.
