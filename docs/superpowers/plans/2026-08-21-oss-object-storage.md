# MathWeaver OSS Object Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist authenticated Web users' task files in private Alibaba Cloud OSS while retaining structured data and ownership metadata in MySQL.

**Architecture:** Add a focused OSS adapter under `backend/storage` and keep `api_v2.py` responsible only for lifecycle orchestration. Each authenticated task receives a deterministic user-scoped object prefix stored on `History`; persistence uploads before committing the corresponding MySQL state, while resume/download restores only after database ownership checks.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2, Alembic, `oss2`, pytest, Alibaba Cloud OSS.

**Spec:** `docs/2026-08-21-oss-storage-requirements.md`

## Global Constraints

- Existing HTTP routes and frontend behavior remain unchanged.
- Real OSS credentials never enter source, tests, documentation, Git, build arguments, or logs.
- Production OSS endpoints use HTTPS and all object keys remain under `mathweaver/users/{user_id}/jobs/{job_id}/`.
- Anonymous and legacy desktop tasks do not use OSS.
- OSS synchronization succeeds before the matching MySQL task state is reported as safely persisted.
- Tests are written and observed failing before production code is added.

---

### Task 1: OSS adapter and configuration boundary

**Files:**
- Create: `backend/storage/object_storage.py`
- Create: `backend/tests/test_object_storage.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `ObjectStorageConfig.from_environment(environment: Mapping[str, str]) -> ObjectStorageConfig | None`
- Produces: `OssObjectStorage(config: ObjectStorageConfig, bucket_factory: Callable | None = None)`
- Produces: `task_prefix(user_id: int, job_id: str) -> str`
- Produces: `sync_job(user_id: int, job_id: str, artifact_root: Path, source_pdf_root: Path) -> str`
- Produces: `restore_job(user_id: int, job_id: str, artifact_root: Path, source_pdf_root: Path) -> bool`
- Produces: `delete_job(user_id: int, job_id: str) -> None`

- [ ] **Step 1: Write failing adapter tests**

```python
def test_oss_config_rejects_incomplete_enabled_configuration():
    with pytest.raises(RuntimeError, match="MATHWEAVER_OSS_BUCKET"):
        ObjectStorageConfig.from_environment({"MATHWEAVER_OBJECT_STORAGE": "oss"})


def test_task_prefix_is_scoped_to_user_and_rejects_unsafe_job_id():
    storage = configured_storage(FakeBucket())
    assert storage.task_prefix(7, "job-1") == "mathweaver/users/7/jobs/job-1/"
    with pytest.raises(ValueError):
        storage.task_prefix(7, "../job-1")


def test_sync_restore_and_delete_round_trip(tmp_path):
    bucket = FakeBucket()
    storage = configured_storage(bucket)
    artifact_root = tmp_path / "jobs" / "job-1"
    source_root = tmp_path / "source" / "job-1"
    (artifact_root / "_stage_cache").mkdir(parents=True)
    (artifact_root / "input.md").write_text("source", encoding="utf-8")
    (artifact_root / "_stage_cache" / "manifest.json").write_text("{}", encoding="utf-8")
    source_root.mkdir(parents=True)
    (source_root / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    prefix = storage.sync_job(7, "job-1", artifact_root, source_root)
    shutil.rmtree(artifact_root)
    shutil.rmtree(source_root)

    assert storage.restore_job(7, "job-1", artifact_root, source_root) is True
    assert (artifact_root / "input.md").read_text(encoding="utf-8") == "source"
    storage.delete_job(7, "job-1")
    assert not bucket.objects_with_prefix(prefix)
```

- [ ] **Step 2: Run the adapter tests and verify RED**

Run: `python -m pytest tests/test_object_storage.py -q` from `backend/`
Expected: collection fails because `storage.object_storage` does not exist.

- [ ] **Step 3: Implement the minimal adapter**

```python
@dataclass(frozen=True)
class ObjectStorageConfig:
    endpoint: str
    bucket: str
    access_key_id: str
    access_key_secret: str
    prefix: str = "mathweaver/"


class OssObjectStorage:
    def task_prefix(self, user_id: int, job_id: str) -> str:
        safe_job_id = _safe_job_id(job_id)
        return f"{self._config.prefix}users/{int(user_id)}/jobs/{safe_job_id}/"
```

The implementation uploads regular files with relative POSIX keys, skips symlinks, `_stage_work`, and `*.tmp`, writes downloads through a temporary sibling followed by `os.replace`, lists only the exact task prefix, and redacts SDK exception details from its public `ObjectStorageError`.

- [ ] **Step 4: Run adapter tests and verify GREEN**

Run: `python -m pytest tests/test_object_storage.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- backend/storage/object_storage.py backend/tests/test_object_storage.py backend/requirements.txt
git commit -m "feat: add private OSS storage adapter"
```

### Task 2: Persist OSS task prefix in MySQL

**Files:**
- Modify: `backend/storage/models.py`
- Modify: `backend/storage/learning_repository.py`
- Create: `backend/migrations/versions/20260821_02_oss_task_prefix.py`
- Modify: `backend/tests/test_database_models.py`
- Modify: `backend/tests/test_learning_storage.py`

**Interfaces:**
- Consumes: `OssObjectStorage.task_prefix(user_id, job_id)`
- Adds: `History.object_storage_prefix: str | None`
- Adds: `JobSnapshot.object_storage_prefix: str | None`
- Produces: history dictionaries containing `object_storage_prefix`

- [ ] **Step 1: Write failing model and repository tests**

```python
def test_history_round_trips_object_storage_prefix(repository, user):
    snapshot = job_snapshot(object_storage_prefix="mathweaver/users/1/jobs/job-1/")
    assert repository.upsert_job_progress(user.id, snapshot) is True
    row = repository.get_owned_history(user.id, snapshot.job_id)
    assert row["object_storage_prefix"] == "mathweaver/users/1/jobs/job-1/"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_database_models.py tests/test_learning_storage.py -q`
Expected: failure because the model and snapshot do not expose `object_storage_prefix`.

- [ ] **Step 3: Add the nullable column and migration**

```python
object_storage_prefix: Mapped[str | None] = mapped_column(String(1024), nullable=True)
```

Migration `20260821_02` uses `down_revision = "20260821_01"`, adds the nullable column on upgrade, and drops only that column on downgrade.

- [ ] **Step 4: Run focused tests and offline migration verification**

Run: `python -m pytest tests/test_database_models.py tests/test_learning_storage.py -q`
Run: `python -m alembic -c migrations/alembic.ini upgrade head --sql`
Expected: tests pass and SQL contains `object_storage_prefix` without any real database name or credential.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- backend/storage/models.py backend/storage/learning_repository.py backend/migrations/versions/20260821_02_oss_task_prefix.py backend/tests/test_database_models.py backend/tests/test_learning_storage.py
git commit -m "feat: persist OSS task ownership metadata"
```

### Task 3: Integrate OSS with task lifecycle

**Files:**
- Modify: `backend/api_v2.py`
- Modify: `backend/tests/test_learning_storage.py`

**Interfaces:**
- Consumes: `ObjectStorageConfig.from_environment`, `OssObjectStorage.sync_job`, `restore_job`, and `delete_job`
- Produces: `_persist_job_with_files(job: dict, status: str, user_id: int | None = None) -> bool`
- Produces: `_restore_job_files(job: dict) -> bool`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_web_job_syncs_oss_before_database_success(authenticated_clients, fake_object_storage):
    client, repository = authenticated_clients.student
    job = persisted_job(user_id=repository.user_id)
    assert api_v2._persist_job_with_files(job, "running") is True
    assert fake_object_storage.synced == [(repository.user_id, job["job_id"])]
    assert repository.get_owned_history(repository.user_id, job["job_id"])["object_storage_prefix"].endswith(f"/{job['job_id']}/")


def test_oss_failure_never_reports_persistence_success(authenticated_clients, failing_object_storage):
    job = persisted_job(user_id=authenticated_clients.student.user_id)
    assert api_v2._persist_job_with_files(job, "done") is False
    assert job["error_code"] == "persistence_error"


def test_restart_export_restores_owned_artifacts(authenticated_clients, fake_object_storage):
    response = authenticated_clients.student.client.post("/api/v2/export/restart-job/artifacts")
    assert response.status_code == 200
    assert fake_object_storage.restored == [(authenticated_clients.student.user_id, "restart-job")]
```

- [ ] **Step 2: Run focused lifecycle tests and verify RED**

Run: `python -m pytest tests/test_learning_storage.py -q`
Expected: failures because lifecycle helpers and OSS restoration do not exist.

- [ ] **Step 3: Implement lifecycle orchestration**

```python
def _persist_job_with_files(job: dict, status: str, user_id: int | None = None) -> bool:
    owner_id = int(user_id if user_id is not None else job.get("_user_id"))
    if _object_storage is not None:
        prefix = _object_storage.sync_job(
            owner_id,
            job["job_id"],
            _persistent_job_dir(job["job_id"]),
            _source_pdf_dir(job["job_id"]),
        )
        job["_object_storage_prefix"] = prefix
    return _upsert_job_history(job, status, owner_id)
```

All existing authenticated Web persistence call sites use the helper. Resume/export/source-PDF restoration occurs only after `_owned_job_resource` confirms ownership. History deletion removes only the authenticated user's task prefix. Desktop and anonymous branches retain their current local behavior.

- [ ] **Step 4: Run lifecycle and authorization tests**

Run: `python -m pytest tests/test_learning_storage.py tests/test_auth_mysql.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- backend/api_v2.py backend/tests/test_learning_storage.py
git commit -m "feat: persist web task files through OSS"
```

### Task 4: Deployment configuration, documentation, and verification

**Files:**
- Modify: `.env.example`
- Create: `backend/scripts/verify_oss_storage.py`
- Modify: `docs/WEB_DEPLOYMENT.md`
- Test: `backend/tests/test_deployment_config.py`
- Test: `backend/tests/test_object_storage.py`

**Interfaces:**
- Consumes: the six `MATHWEAVER_OBJECT_STORAGE` and `MATHWEAVER_OSS_*` environment variables from the spec
- Produces: OSS values loaded only from the existing backend Docker Secret without frontend/build-time exposure

- [ ] **Step 1: Write failing deployment configuration test**

```python
def test_oss_configuration_stays_inside_backend_runtime_secret():
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "deploy" / "docker-compose.web.yml").read_text(encoding="utf-8")
    assert "MATHWEAVER_OBJECT_STORAGE=local" in example
    assert "MATHWEAVER_OSS_ACCESS_KEY_SECRET=" in example
    assert "${MATHWEAVER_OSS_ACCESS_KEY_SECRET" not in compose
    assert "target: mathweaver_backend.env" in compose
```

- [ ] **Step 2: Run deployment test and verify RED**

Run: `python -m pytest tests/test_deployment_config.py -q`
Expected: failure because `.env.example` does not yet document OSS configuration.

- [ ] **Step 3: Add safe runtime configuration and operator steps**

`.env.example` contains empty credential values and safe local/prefix defaults. The existing Compose backend secret mount loads the values at runtime and remains unchanged. `WEB_DEPLOYMENT.md` documents private Bucket policy, RAM prefix policy, migration order, protected environment file permissions, and `verify_oss_storage.py`, which uploads, restores, compares, and removes one random test object without printing its key or credentials.

- [ ] **Step 4: Run complete scoped verification**

Run: `python -m pytest tests/test_object_storage.py tests/test_database_models.py tests/test_learning_storage.py tests/test_deployment_config.py tests/test_deployment_cors.py -q`
Run: `npm exec -- vitest run`
Run: `npm run typecheck`
Run: `npm run build`
Expected: all scoped backend and frontend checks pass.

- [ ] **Step 5: Run real OSS smoke verification**

Run the documented smoke command with production credentials supplied only through the protected server environment. Expected behavior: upload succeeds under a random `mathweaver/users/1/jobs/smoke-*` prefix, downloaded bytes match, deletion succeeds, and the object no longer exists.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- .env.example backend/scripts/verify_oss_storage.py docs/WEB_DEPLOYMENT.md backend/tests/test_deployment_config.py backend/tests/test_object_storage.py docs/2026-08-21-oss-storage-requirements.md docs/superpowers/plans/2026-08-21-oss-object-storage.md
git commit -m "docs: complete OSS deployment contract"
```

## Plan self-review

- Spec coverage: configuration, isolation, sync-before-DB, restore, delete, migration, deployment, and real smoke verification each map to a task.
- Placeholder scan: every task identifies exact files, interfaces, tests, commands, and expected outcomes; no deferred implementation step remains.
- Type consistency: `object_storage_prefix` is the single metadata name across model, snapshot, repository, API job state, migration, and tests.
