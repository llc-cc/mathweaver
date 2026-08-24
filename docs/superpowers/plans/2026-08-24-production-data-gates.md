# Production Data Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deployment, CI and operational gates that prove the backend can migrate, build, start and enforce production storage configuration.

**Architecture:** A dedicated GitHub Actions workflow runs backend, MySQL, Fake OSS, TypeScript and image gates. Production Compose explicitly forces OSS and runs a storage worker. Operations documents and verification scripts make external RDS/OSS checks repeatable without placing cloud credentials in public CI.

**Tech Stack:** GitHub Actions, Docker Compose, MySQL 8, pytest, Alembic, TypeScript, PowerShell/Bash-compatible documented commands.

**Spec:** `docs/superpowers/specs/2026-08-24-data-storage-production-hardening-design.md`

## Global Constraints

- This plan owns `.github/workflows`, `deploy`, `.env.example`, deployment tests and operations documents.
- Do not modify `backend/storage/*`, `backend/api_v2.py`, `backend/storage/models.py`, or Alembic revisions; the core-data implementation owns them.
- Public CI uses Fake OSS only and never receives long-lived OSS/RDS production credentials.
- Production Compose must fail closed with OSS while tests retain an explicit local-storage configuration.
- Every secret remains backend-only and is not copied into frontend build arguments or images.

---

### Task 1: Production Compose storage contract

**Files:**
- Modify: `deploy/docker-compose.web.yml`
- Create: `deploy/docker-compose.test.yml`
- Modify: `.env.example`
- Modify: `backend/tests/test_deployment_config.py`

**Interfaces:**
- Consumes core entry points: `python scripts/production_migrate.py` and `python -m storage.storage_worker` from the backend image workdir.
- Produces production environment `MATHWEAVER_OBJECT_STORAGE=oss` for backend and worker.

- [ ] **Step 1: Add failing static deployment tests**

```python
def test_production_compose_forces_oss_and_runs_worker():
    compose = read_compose("docker-compose.web.yml")
    assert "MATHWEAVER_OBJECT_STORAGE: oss" in compose
    assert "storage-worker:" in compose
    assert "python" in compose and "-m" in compose and "storage.storage_worker" in compose
```

- [ ] **Step 2: Run deployment tests and confirm failure**

Run: `python -m pytest backend/tests/test_deployment_config.py -q`

Expected: FAIL because production currently defaults to local when the secret omits the variable.

- [ ] **Step 3: Add explicit production OSS environment and worker service**

The migration service runs `python scripts/production_migrate.py`. The worker uses the backend image, backend-only secret, artifact volume, restart policy and migration dependency. The test override explicitly sets local storage and disables the worker so health smoke tests need no cloud credentials.

- [ ] **Step 4: Document exact credential/capacity environment names without values**

`.env.example` lists OSS endpoint/bucket/access credentials, `MATHWEAVER_CREDENTIAL_KEYS_JSON`, `MATHWEAVER_CREDENTIAL_ACTIVE_KEY_ID`, capacity limits and metrics bind configuration. Values remain empty examples.

- [ ] **Step 5: Run deployment tests and commit**

Run: `python -m pytest backend/tests/test_deployment_config.py -q`

Expected: PASS.

```bash
git add deploy/docker-compose.web.yml deploy/docker-compose.test.yml .env.example backend/tests/test_deployment_config.py
git commit -m "deploy: enforce production object storage"
```

### Task 2: Backend/MySQL quality workflow

**Files:**
- Create: `.github/workflows/backend-quality.yml`
- Modify: `backend/tests/test_deployment_config.py`

**Interfaces:**
- Produces required CI jobs: `backend-tests`, `mysql-integration`, `frontend-types`, `container-smoke`.

- [ ] **Step 1: Add a failing workflow-structure test**

```python
def test_backend_quality_workflow_contains_release_gates():
    workflow = (PROJECT_ROOT / ".github/workflows/backend-quality.yml").read_text()
    for gate in ("pytest", "alembic", "mysql", "tsc --noEmit", "docker build", "health"):
        assert gate in workflow.lower()
```

- [ ] **Step 2: Run the deployment test and confirm the workflow is missing**

Run: `python -m pytest backend/tests/test_deployment_config.py -q`

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Create the workflow with pinned major actions and a MySQL 8 service**

`backend-tests` installs `backend/requirements.txt`, compiles storage modules and runs all pytest. `mysql-integration` waits for MySQL, runs Alembic upgrade, generates offline SQL and runs tests marked `mysql`. `frontend-types` runs `npm ci`, tests and `npx tsc --noEmit`.

- [ ] **Step 4: Add container build and health smoke**

Build backend and frontend images, start the test Compose override, wait with a bounded loop for `/health`, print service logs on failure, and always run `docker compose down --volumes` in a final step.

- [ ] **Step 5: Run static tests and locally available commands**

Run: `python -m pytest backend/tests/test_deployment_config.py -q`

Run: `npx tsc --noEmit`

Expected: PASS.

- [ ] **Step 6: Commit workflow**

```bash
git add .github/workflows/backend-quality.yml backend/tests/test_deployment_config.py
git commit -m "ci: gate backend storage and container releases"
```

### Task 3: Backup, recovery and capacity runbooks

**Files:**
- Create: `docs/operations/DATA_BACKUP_RECOVERY.md`
- Create: `docs/operations/STORAGE_CAPACITY_AND_ALERTS.md`
- Create: `deploy/verify-restored-data.ps1`
- Modify: `docs/WEB_DEPLOYMENT.md`

**Interfaces:**
- Produces operator procedure accepting database URL via environment and OSS verification through the backend verification command; no credentials on command line.

- [ ] **Step 1: Write the recovery runbook with measurable acceptance criteria**

Specify the owner and evidence for RDS automatic backups, retention, point-in-time restore, OSS versioning/replication, quarterly restore drills, measured RPO/RTO, row counts, per-status counts, manifest checksum samples and object hash samples.

- [ ] **Step 2: Add a non-secret verification wrapper**

```powershell
param([Parameter(Mandatory=$true)][int]$ExpectedHistoryRows)
if (-not $env:MATHWEAVER_DATABASE_URL) { throw 'MATHWEAVER_DATABASE_URL is required' }
python backend/scripts/verify_restored_data.py --expected-history-rows $ExpectedHistoryRows
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

The wrapper must never echo the connection URL or OSS credentials.

- [ ] **Step 3: Document capacity ownership and alert thresholds**

Cover per-user/task limits, retention, OSS lifecycle ordering after soft delete, orphan grace period, disk free-space alerts, outbox backlog/failure alerts, restore latency, task bytes/files, database transaction failures and connection pool saturation.

- [ ] **Step 4: Link runbooks from Web deployment documentation and commit**

```bash
git add docs/operations/DATA_BACKUP_RECOVERY.md docs/operations/STORAGE_CAPACITY_AND_ALERTS.md deploy/verify-restored-data.ps1 docs/WEB_DEPLOYMENT.md
git commit -m "docs: add data recovery and capacity runbooks"
```

### Task 4: External-plan verification and handoff

**Files:**
- Modify only files owned by this plan when verification exposes a defect.

- [ ] **Step 1: Verify the plan did not touch core-data-owned files**

Run: `git diff --name-only 10661b9...HEAD`

Expected: no paths under `backend/storage/`, no `backend/api_v2.py`, no `backend/storage/models.py`, and no `backend/migrations/versions/` paths.

- [ ] **Step 2: Run deployment tests and TypeScript checking**

Run: `python -m pytest backend/tests/test_deployment_config.py -q`

Run: `npx tsc --noEmit`

Expected: PASS.

- [ ] **Step 3: Produce a handoff note**

Report branch name, commit hashes, changed paths, tests executed, commands not executed, and any interface assumption about `storage.storage_worker`. Do not merge into the core branch; hand the branch to the integration owner.
