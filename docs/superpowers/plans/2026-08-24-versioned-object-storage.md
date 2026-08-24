# Versioned Object Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace overwrite-style MySQL/OSS dual writes with immutable verified versions, database pointers, soft deletion and a retryable outbox.

**Architecture:** `OssObjectStorage` uploads immutable version directories and commits each with a canonical manifest. `LearningRepository` atomically switches the database pointer and enqueues cleanup. A dedicated worker and reconciliation CLI process idempotent cleanup and report drift.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, Flask, oss2-compatible bucket API, SHA-256, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-data-storage-production-hardening-design.md`

## Global Constraints

- Never overwrite the version currently referenced by MySQL.
- A version is readable only after its manifest exists and every file verifies.
- Delete is database soft delete plus transactional outbox; API requests do not physically delete OSS.
- SDK exception details, credentials and signed URLs never enter responses, logs or outbox rows.
- All object keys and local restore targets remain under authenticated task ownership.

---

### Task 1: Immutable version manifest

**Files:**
- Modify: `backend/storage/object_storage.py`
- Rewrite focused expectations in: `backend/tests/test_object_storage.py`

**Interfaces:**
- Produces: `StoredVersion(version_id: str, prefix: str, manifest_checksum: str, file_count: int, total_bytes: int)`.
- Produces: `upload_version(user_id, job_id, artifact_root, source_pdf_root) -> StoredVersion`.
- Produces: `verify_version(user_id, job_id, version_id, expected_checksum) -> StoredVersion`.

- [ ] **Step 1: Write failing tests for version isolation and manifest-last commit**

```python
def test_upload_creates_immutable_version_and_manifest_last(storage, bucket, roots):
    stored = storage.upload_version(7, "job-1", *roots)
    assert f"/versions/{stored.version_id}/" in stored.prefix
    assert bucket.put_order[-1] == f"{stored.prefix}manifest.json"
    assert storage.verify_version(7, "job-1", stored.version_id, stored.manifest_checksum) == stored
```

- [ ] **Step 2: Run object-storage tests and confirm the old overwrite contract fails**

Run: `python -m pytest backend/tests/test_object_storage.py -q`

Expected: FAIL because `upload_version` and `StoredVersion` do not exist.

- [ ] **Step 3: Implement canonical manifest generation and immutable upload**

Canonical JSON uses UTF-8, `sort_keys=True`, and separators `(",", ":")`. Each file entry contains only `path`, `size`, and lowercase SHA-256. Upload data files first, validate returned/HEAD size, then upload `manifest.json`.

- [ ] **Step 4: Add failure tests for upload interruption, missing manifest, unsafe paths, owner mismatch, size mismatch and hash tampering**

```python
@pytest.mark.parametrize("mutation", ["missing-manifest", "bad-owner", "bad-size", "bad-hash"])
def test_verify_rejects_uncommitted_or_corrupt_version(storage, committed_version, mutation):
    mutate_fake_bucket(committed_version, mutation)
    with pytest.raises(ObjectStorageError):
        storage.verify_version(7, "job-1", committed_version.version_id, committed_version.manifest_checksum)
```

- [ ] **Step 5: Run object-storage tests and commit**

Run: `python -m pytest backend/tests/test_object_storage.py -q`

Expected: PASS.

```bash
git add backend/storage/object_storage.py backend/tests/test_object_storage.py
git commit -m "feat: store immutable verified OSS versions"
```

### Task 2: Storage state and outbox schema

**Files:**
- Create: `backend/migrations/versions/20260824_04_storage_state_outbox.py`
- Modify: `backend/storage/models.py`
- Modify: `backend/tests/test_database_models.py`

**Interfaces:**
- Produces: `History.storage_version`, `storage_status`, `storage_checksum`, `storage_file_count`, `storage_bytes`, `deleted_at`.
- Produces: `StorageOutbox` with unique `idempotency_key`, operation, status, attempts, next attempt and lease fields.

- [ ] **Step 1: Add failing metadata and constraint tests**

```python
def test_storage_outbox_idempotency_key_is_unique(session):
    session.add_all([outbox("delete_version", "same-key"), outbox("delete_version", "same-key")])
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run model tests and confirm missing columns/table**

Run: `python -m pytest backend/tests/test_database_models.py -q`

Expected: FAIL.

- [ ] **Step 3: Add model fields, indexes and Alembic upgrade/downgrade**

Use bounded `String` status/operation columns, unsigned-compatible non-negative counters, UTC timestamps, an index on `(status, next_attempt_at)`, and a unique index on `idempotency_key`. Existing history rows receive `storage_status='legacy'`.

- [ ] **Step 4: Run model tests and Alembic offline SQL generation**

Run: `python -m pytest backend/tests/test_database_models.py -q`

Run: `cd backend && python -m alembic -c migrations/alembic.ini upgrade head --sql`

Expected: tests PASS and SQL includes the new history columns and `storage_outbox` table.

- [ ] **Step 5: Commit schema**

```bash
git add backend/migrations/versions/20260824_04_storage_state_outbox.py backend/storage/models.py backend/tests/test_database_models.py
git commit -m "feat: add storage state and outbox schema"
```

### Task 3: Atomic pointer switch, summary projection and soft delete

**Files:**
- Modify: `backend/storage/learning_repository.py`
- Modify: `backend/tests/test_learning_storage.py`

**Interfaces:**
- Consumes: `StoredVersion` from Task 1.
- Produces: `commit_storage_version(user_id, snapshot, stored_version) -> bool`.
- Produces: `soft_delete_history(user_id, history_id) -> bool`.
- Produces: `list_history` using explicit projected columns and `deleted_at IS NULL`.

- [ ] **Step 1: Write failing transaction and projection tests**

```python
def test_version_switch_and_old_cleanup_outbox_commit_together(repository, stored_version):
    repository.commit_storage_version(1, snapshot("job-1"), stored_version)
    row = repository.get_owned_history(1, "job-1")
    assert row["storage_version"] == stored_version.version_id
    assert repository.pending_outbox_operations("job-1") == ["delete_version"]
```

Capture emitted SQL for `list_history` and assert it omits `nodes_json`, `edges_json`, `source_markdown`, and `source_pdf_json`.

- [ ] **Step 2: Run repository tests and confirm failures**

Run: `python -m pytest backend/tests/test_learning_storage.py -q`

Expected: FAIL on missing repository methods and large-column projection.

- [ ] **Step 3: Implement one-session pointer switch/outbox creation and soft delete**

On switch, enqueue deletion only when an older different version exists. On soft delete, update `deleted_at` and `storage_status='delete_pending'`, then enqueue `delete_job_versions` in the same session. Ordinary reads always filter `deleted_at IS NULL`.

- [ ] **Step 4: Add rollback and idempotency tests**

```python
def test_failed_database_commit_leaves_old_version_visible(repository, fail_next_commit):
    before = repository.get_owned_history(1, "job-1")["storage_version"]
    with pytest.raises(SQLAlchemyError):
        repository.commit_storage_version(1, next_snapshot(), next_version())
    assert repository.get_owned_history(1, "job-1")["storage_version"] == before
```

- [ ] **Step 5: Run repository tests and commit**

Run: `python -m pytest backend/tests/test_learning_storage.py -q`

Expected: PASS.

```bash
git add backend/storage/learning_repository.py backend/tests/test_learning_storage.py
git commit -m "feat: atomically switch task storage versions"
```

### Task 4: Outbox processor and reconciliation report

**Files:**
- Create: `backend/storage/storage_worker.py`
- Create: `backend/storage/reconciliation.py`
- Create: `backend/scripts/reconcile_storage.py`
- Create: `backend/tests/test_storage_worker.py`
- Create: `backend/tests/test_storage_reconciliation.py`

**Interfaces:**
- Produces: `StorageOutboxProcessor.run_once(now: datetime) -> ProcessingSummary`.
- Produces: `StorageReconciler.scan() -> ReconciliationReport`.
- Produces: CLI default read-only; `--enqueue-repairs` only creates idempotent outbox rows.

- [ ] **Step 1: Add failing lease, retry and idempotency tests**

```python
def test_failed_delete_retries_with_bounded_backoff(processor, clock):
    summary = processor.run_once(clock.now)
    row = load_outbox()
    assert summary.failed == 1
    assert row.attempts == 1
    assert row.status == "pending"
    assert row.next_attempt_at > clock.now
    assert "credential" not in (row.last_error_code or "").lower()
```

- [ ] **Step 2: Run new tests and confirm import failures**

Run: `python -m pytest backend/tests/test_storage_worker.py backend/tests/test_storage_reconciliation.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement lease acquisition, bounded exponential retry and stable error codes**

Use database time, finite leases and compare-and-set status updates. MySQL uses `SELECT ... FOR UPDATE SKIP LOCKED`; SQLite tests use a deterministic single-consumer path. Missing objects count as successful deletion.

After `delete_job_versions` and `delete_local_cache` both succeed, update the soft-deleted history row to `storage_status='deleted'`. A failed or leased companion operation keeps it at `delete_pending`.

- [ ] **Step 4: Implement read-only drift scan and explicit repair enqueueing**

The report contains counts and opaque task/version IDs, never SDK exception strings. Orphans younger than the configured grace period are ignored. Repair mode writes outbox rows rather than deleting directly.

- [ ] **Step 5: Run worker/reconciliation tests and commit**

Run: `python -m pytest backend/tests/test_storage_worker.py backend/tests/test_storage_reconciliation.py -q`

Expected: PASS.

```bash
git add backend/storage/storage_worker.py backend/storage/reconciliation.py backend/scripts/reconcile_storage.py backend/tests/test_storage_worker.py backend/tests/test_storage_reconciliation.py
git commit -m "feat: reconcile storage through retryable outbox"
```

### Task 5: Verified restore and API lifecycle integration

**Files:**
- Modify: `backend/storage/object_storage.py`
- Modify: `backend/api_v2.py`
- Modify: `backend/tests/test_object_storage.py`
- Modify: `backend/tests/test_learning_storage.py`

**Interfaces:**
- Produces: `restore_version(user_id, job_id, version_id, expected_checksum, artifact_root, source_pdf_root) -> bool`.
- Consumes: repository pointer switch and soft delete methods.

- [ ] **Step 1: Add failing restore rollback and API soft-delete tests**

```python
def test_corrupt_restore_keeps_existing_cache(storage, existing_cache, corrupt_version):
    with pytest.raises(ObjectStorageError):
        storage.restore_version(7, "job-1", corrupt_version.id, corrupt_version.checksum, *existing_cache.roots)
    assert existing_cache.read_marker() == "old-cache"
```

- [ ] **Step 2: Run focused tests and confirm old restore/delete behavior fails**

Run: `python -m pytest backend/tests/test_object_storage.py backend/tests/test_learning_storage.py -q`

Expected: FAIL because restore is per-file overwrite and API deletion is physical-first.

- [ ] **Step 3: Implement staged verified restore with task lock, backup and rollback**

Download all files to sibling staging directories, verify every digest, move existing roots to bounded backup names, install both new roots, and restore backups on any install error. Cleanup of staging/backups is best effort and never changes database state.

- [ ] **Step 4: Change persistence and deletion handlers**

`_persist_job_with_files` uploads a version then commits its pointer. `_cancel_job_record` stops the runtime and calls soft delete; it does not delete OSS or local directories. Details return 409 with a stable storage state when the referenced version is unavailable.

- [ ] **Step 5: Run focused and full backend tests**

Run: `python -m pytest backend/tests/test_object_storage.py backend/tests/test_learning_storage.py -q`

Run: `python -m pytest backend/tests -q`

Expected: PASS.

- [ ] **Step 6: Commit lifecycle integration**

```bash
git add backend/storage/object_storage.py backend/api_v2.py backend/tests/test_object_storage.py backend/tests/test_learning_storage.py
git commit -m "fix: make task storage lifecycle recoverable"
```

### Task 6: Promote legacy mutable prefixes

**Files:**
- Create: `backend/scripts/migrate_legacy_storage.py`
- Create: `backend/tests/test_legacy_storage_migration.py`

**Interfaces:**
- Produces: `promote_legacy_history(repository, storage, *, apply: bool) -> PromotionSummary`.

- [ ] **Step 1: Add failing dry-run, promotion and idempotency tests**

```python
def test_legacy_promotion_copies_to_version_before_switch(repository, storage):
    summary = promote_legacy_history(repository, storage, apply=True)
    row = repository.get_owned_history(1, "legacy-job")
    assert summary.promoted == 1
    assert row["storage_status"] == "ready"
    assert row["storage_version"]
    assert storage.verify_version(1, "legacy-job", row["storage_version"], row["storage_checksum"])
```

- [ ] **Step 2: Run the focused test and confirm the command is missing**

Run: `python -m pytest backend/tests/test_legacy_storage_migration.py -q`

Expected: FAIL during import.

- [ ] **Step 3: Implement read-only reporting and explicit promotion**

Enumerate only non-deleted history rows with `object_storage_prefix` and no `storage_version`. In apply mode, download the authenticated legacy prefix to controlled temporary roots, upload and verify an immutable version, atomically switch the database pointer, and enqueue legacy-prefix cleanup only after the switch commits.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m pytest backend/tests/test_legacy_storage_migration.py -q`

Expected: PASS.

```bash
git add backend/scripts/migrate_legacy_storage.py backend/tests/test_legacy_storage_migration.py
git commit -m "feat: promote legacy OSS tasks to immutable versions"
```
