# Data Operations Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete production capacity enforcement, security auditing, low-cardinality metrics and restored-data verification after credential and storage foundations merge.

**Architecture:** Focused modules own capacity parsing, audit allowlists and metric instruments. Existing service transactions receive audit writes through a session-aware helper. API/storage boundaries report stable metrics without user identifiers, and a verification command compares restored database state with committed manifests.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2, Prometheus client, MySQL, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-data-storage-production-hardening-design.md`

## Global Constraints

- Execute this plan only after credential-security and versioned-object-storage plans are integrated.
- Capacity failures occur before MySQL or OSS partial writes and return stable 413, 422 or 507 responses.
- Audit and metric labels never contain secrets, user IDs, task IDs, object keys, URLs or exception text.
- Public Nginx must not proxy the internal metrics endpoint.
- Add concise Chinese comments for security, transaction and capacity boundaries.

---

### Task 1: Validated capacity configuration

**Files:**
- Create: `backend/storage/capacity.py`
- Create: `backend/tests/test_capacity.py`
- Modify: `backend/api_v2.py`
- Modify: `backend/tests/test_learning_storage.py`

**Interfaces:**
- Produces: `CapacityLimits.from_environment(environment: Mapping[str, str]) -> CapacityLimits`.
- Produces: `validate_history_payload(nodes, edges, source_markdown, source_pdf) -> PayloadSize`.
- Produces: `ensure_disk_capacity(path: Path, required_bytes: int) -> None`.
- Produces: `ensure_user_storage_capacity(repository, user_id: int, incoming_bytes: int) -> None`.
- Produces: `CapacityExceeded(code: str, http_status: int)` without payload data.

- [ ] **Step 1: Add failing tests for default bounds, invalid environment values, JSON bytes and disk reserve**

```python
def test_history_json_limit_uses_utf8_bytes():
    limits = CapacityLimits(max_history_json_bytes=8, **minimum_valid_limits())
    with pytest.raises(CapacityExceeded) as caught:
        limits.validate_history_payload([{"x": "数学"}], [], None, None)
    assert caught.value.code == "history_json_too_large"
    assert caught.value.http_status == 422
```

Add a repository-backed test proving `existing non-deleted storage_bytes + incoming_bytes` cannot exceed `max_user_history_bytes`, while replacing the same job subtracts its current bytes before comparison.

- [ ] **Step 2: Run focused tests and confirm missing module failure**

Run: `python -m pytest backend/tests/test_capacity.py -q`

Expected: FAIL during import.

- [ ] **Step 3: Implement positive integer parsing and bounded payload/disk checks**

Reject booleans, zero, negatives and unsafe combinations at startup. Serialize with the same UTF-8/canonical options used by persistence. Use `shutil.disk_usage` and require `free - required_bytes >= min_free_disk_bytes`.

- [ ] **Step 4: Apply limits at upload and persistence boundaries**

Set Flask `MAX_CONTENT_LENGTH`; validate node/edge counts, encoded bytes and per-user non-deleted `storage_bytes` before object upload or repository mutation. Map `CapacityExceeded` to stable JSON and never echo content. Add a retention query that selects expired, non-deleted history IDs and enqueues their existing soft-delete operation; it must not perform physical deletion inside the scan.

- [ ] **Step 5: Run capacity/API tests and commit**

Run: `python -m pytest backend/tests/test_capacity.py backend/tests/test_learning_storage.py -q`

Expected: PASS.

```bash
git add backend/storage/capacity.py backend/tests/test_capacity.py backend/api_v2.py backend/tests/test_learning_storage.py
git commit -m "feat: enforce production data capacity limits"
```

### Task 2: MySQL packet and restored-data preflight

**Files:**
- Create: `backend/scripts/verify_restored_data.py`
- Create: `backend/tests/test_data_preflight.py`
- Modify: `backend/storage/database.py`

**Interfaces:**
- Produces: `validate_mysql_packet(engine, required_bytes: int) -> int`.
- Produces CLI options `--expected-history-rows`, `--sample-size`, `--verify-objects`.

- [ ] **Step 1: Add failing packet-size and secret-free report tests**

```python
def test_packet_preflight_rejects_limit_below_payload(fake_engine):
    fake_engine.scalar_value = 4 * 1024 * 1024
    with pytest.raises(RuntimeError, match="max_allowed_packet is below"):
        validate_mysql_packet(fake_engine, required_bytes=5 * 1024 * 1024)
```

- [ ] **Step 2: Run focused tests and confirm missing functions**

Run: `python -m pytest backend/tests/test_data_preflight.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement MySQL preflight and restored-data verifier**

Query `SELECT @@max_allowed_packet`, compare against the configured maximum encoded history payload plus a fixed 1 MiB safety margin, and return only numeric limits. The verifier checks total/non-deleted/deleted history counts, storage status counts, manifest checksums for a deterministic sample, and optional object hashes through `OssObjectStorage`.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest backend/tests/test_data_preflight.py -q`

Expected: PASS.

```bash
git add backend/scripts/verify_restored_data.py backend/tests/test_data_preflight.py backend/storage/database.py
git commit -m "feat: verify database packet and restored data integrity"
```

### Task 3: Allowlisted transactional security audit

**Files:**
- Create: `backend/storage/audit_service.py`
- Create: `backend/tests/test_audit_service.py`
- Modify: `backend/storage/auth_repository.py`
- Modify: `backend/api_v2.py`
- Modify: `backend/tests/test_auth_mysql.py`
- Modify: `backend/tests/test_admin_authorization.py`
- Modify: `backend/tests/test_admin_user_import.py`

**Interfaces:**
- Produces: `AuditWriter.add(session, *, actor_id, action, subject_type, subject_id, details) -> None`.
- Produces: `AuditService.record(...) -> None` for events without an existing transaction.

- [ ] **Step 1: Add failing allowlist and redaction tests**

```python
def test_audit_rejects_secret_details(session):
    with pytest.raises(ValueError, match="audit detail field is not allowed"):
        AuditWriter().add(session, actor_id=1, action="settings.update",
                          subject_type="user", subject_id="1",
                          details={"api_key": "sk-secret"})
```

- [ ] **Step 2: Run focused audit tests and confirm missing service failure**

Run: `python -m pytest backend/tests/test_audit_service.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement fixed action schemas and session-aware writer**

Define exact detail keys for login result/reason, password change, reset, user status, import counts, settings changed config count, history delete and reconciliation repair. Apply `redact_sensitive` before validation as defense in depth; reject unknown actions and fields.

- [ ] **Step 4: Wire mutation events into their existing SQLAlchemy transaction**

Admin password reset, account status, import, settings change and soft delete add audit rows before the surrounding session commits. Audit insertion failure rolls back the mutation. Login success/failure records a separate event with the same public response for nonexistent and wrong-password cases.

- [ ] **Step 5: Run auth/admin/audit tests and commit**

Run: `python -m pytest backend/tests/test_audit_service.py backend/tests/test_auth_mysql.py backend/tests/test_admin_authorization.py backend/tests/test_admin_user_import.py -q`

Expected: PASS.

```bash
git add backend/storage/audit_service.py backend/tests/test_audit_service.py backend/storage/auth_repository.py backend/api_v2.py backend/tests/test_auth_mysql.py backend/tests/test_admin_authorization.py backend/tests/test_admin_user_import.py
git commit -m "feat: persist allowlisted security audit events"
```

### Task 4: Internal operational metrics

**Files:**
- Create: `backend/storage/metrics.py`
- Create: `backend/tests/test_metrics.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/storage/database.py`
- Modify: `backend/storage/object_storage.py`
- Modify: `backend/storage/storage_worker.py`
- Modify: `backend/api_v2.py`

**Interfaces:**
- Produces counters/histograms/gauges prefixed `mathweaver_`.
- Produces internal GET `/internal/metrics` on the backend service only.

- [ ] **Step 1: Add failing metric names, low-cardinality and endpoint tests**

```python
def test_metrics_do_not_accept_resource_identifiers(metrics):
    metrics.record_storage_failure(operation="restore", code="checksum_mismatch")
    payload = metrics.render().decode()
    assert "mathweaver_storage_failures_total" in payload
    assert "job-" not in payload and "users/" not in payload
```

- [ ] **Step 2: Run focused tests and confirm missing module failure**

Run: `python -m pytest backend/tests/test_metrics.py -q`

Expected: FAIL.

- [ ] **Step 3: Add Prometheus instruments and safe call sites**

Instrument session rollback, SQLAlchemy pool checkout/checkin, OSS operation result, restore seconds, outbox status, reconciliation drift counts and storage bytes/files. Labels are validated against finite operation/result sets.

- [ ] **Step 4: Expose the internal endpoint and prove Nginx does not proxy it**

The endpoint returns Prometheus content type. Existing Nginx routes only `/api/v2/`, `/health` and frontend fallback, so `/internal/metrics` is reachable only by addressing the backend container directly; deployment tests assert no explicit proxy location is added.

- [ ] **Step 5: Run metric and deployment tests and commit**

Run: `python -m pytest backend/tests/test_metrics.py backend/tests/test_deployment_config.py -q`

Expected: PASS.

```bash
git add backend/storage/metrics.py backend/tests/test_metrics.py backend/requirements.txt backend/storage/database.py backend/storage/object_storage.py backend/storage/storage_worker.py backend/api_v2.py
git commit -m "feat: expose private storage health metrics"
```

### Task 5: Integrated release verification

**Files:**
- Modify only files required to fix integration regressions.

- [ ] **Step 1: Run backend tests, frontend tests and type checking**

Run: `python -m pytest backend/tests -q`

Run: `npm test -- --run`

Run: `npx tsc --noEmit`

Expected: PASS.

- [ ] **Step 2: Generate migration SQL and compile core Python modules**

Run: `cd backend && python -m alembic -c migrations/alembic.ini upgrade head --sql`

Run: `python -m compileall -q backend/storage backend/scripts backend/api_v2.py`

Expected: both commands exit zero.

- [ ] **Step 3: Verify production configuration contract**

Run: `python -m pytest backend/tests/test_deployment_config.py backend/tests/test_docker_entrypoint.py -q`

Expected: PASS and tests prove production OSS/encryption fail closed.

- [ ] **Step 4: Commit integration-only fixes**

```bash
git add backend app deploy .github docs
git commit -m "test: complete production data release gates"
```
