# Credential Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encrypt Web model credentials at rest, expose only masked settings, preserve existing keys on blank updates, and prevent secrets from entering durable outputs.

**Architecture:** `CredentialCipher` owns authenticated encryption and key rotation. `LearningRepository` separates public descriptors from encrypted runtime secrets; HTTP handlers use separate public/runtime methods. A restart-safe migration command converts legacy rows transactionally and blanks plaintext columns.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2, Alembic, `cryptography` AESGCM, pytest, React Router/TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-24-data-storage-production-hardening-design.md`

## Global Constraints

- Production must fail closed when credential keys are absent or invalid.
- Public JSON, logs, audit details, task snapshots and exports must never contain complete secrets.
- Existing HTTP paths remain unchanged; persisted configurations receive server-owned `config_id` values.
- Legacy plaintext is blanked only in the same transaction that stores valid ciphertext.
- Add concise Chinese comments for security boundaries and non-obvious migration behavior.

---

### Task 1: Authenticated credential cipher

**Files:**
- Create: `backend/storage/credential_crypto.py`
- Create: `backend/tests/test_credential_crypto.py`

**Interfaces:**
- Produces: `CredentialKeyring.from_environment(environment: Mapping[str, str]) -> CredentialKeyring`
- Produces: `CredentialCipher.encrypt_json(value: dict, *, aad: str) -> dict`
- Produces: `CredentialCipher.decrypt_json(envelope: dict, *, aad: str) -> dict`
- Produces: `CredentialConfigurationError` and `CredentialDecryptionError` with secret-free messages.

- [ ] **Step 1: Write failing tests for key parsing, AES-GCM round trip, AAD binding, tamper rejection and old-key reads**

```python
def test_cipher_reads_old_key_and_writes_active_key():
    keys = {"old": b"o" * 32, "current": b"c" * 32}
    old = CredentialCipher(CredentialKeyring(keys, "old"))
    current = CredentialCipher(CredentialKeyring(keys, "current"))
    envelope = old.encrypt_json({"api_key": "sk-secret"}, aad="user:7:llm-settings:v1")
    assert current.decrypt_json(envelope, aad="user:7:llm-settings:v1") == {"api_key": "sk-secret"}
    assert current.encrypt_json({"api_key": "next"}, aad="user:7:llm-settings:v1")["key_id"] == "current"
```

- [ ] **Step 2: Run the focused test and confirm it fails because the module does not exist**

Run: `python -m pytest backend/tests/test_credential_crypto.py -q`

Expected: FAIL during import of `storage.credential_crypto`.

- [ ] **Step 3: Implement strict key parsing and versioned AES-GCM envelopes**

```python
@dataclass(frozen=True)
class CredentialKeyring:
    keys: Mapping[str, bytes]
    active_key_id: str

class CredentialCipher:
    def encrypt_json(self, value: dict, *, aad: str) -> dict:
        nonce = os.urandom(12)
        plaintext = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ciphertext = AESGCM(self._keyring.keys[self._keyring.active_key_id]).encrypt(
            nonce, plaintext, aad.encode()
        )
        return {"version": 1, "key_id": self._keyring.active_key_id,
                "nonce": _b64(nonce), "ciphertext": _b64(ciphertext)}
```

- [ ] **Step 4: Run cipher tests and syntax compilation**

Run: `python -m pytest backend/tests/test_credential_crypto.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated cipher**

```bash
git add backend/storage/credential_crypto.py backend/tests/test_credential_crypto.py
git commit -m "feat: encrypt stored model credentials"
```

### Task 2: Schema and restart-safe plaintext migration

**Files:**
- Create: `backend/migrations/versions/20260824_03_secure_llm_credentials.py`
- Modify: `backend/storage/models.py`
- Create: `backend/scripts/migrate_llm_credentials.py`
- Create: `backend/scripts/production_migrate.py`
- Create: `backend/tests/test_credential_migration.py`
- Modify: `backend/tests/test_database_models.py`

**Interfaces:**
- Consumes: `CredentialCipher.encrypt_json(..., aad=f"user:{user_id}:llm-settings:v1")`
- Produces: `UserSettings.llm_secrets_encrypted_json: dict | None`
- Produces: `migrate_settings(session_factory, cipher, *, apply: bool) -> MigrationSummary`.
- Produces: production entry point that runs Alembic to head, applies credential migration and exits nonzero while plaintext remains.

- [ ] **Step 1: Add failing model and migration tests**

```python
def test_apply_migration_encrypts_and_blanks_legacy_key(session_factory, cipher):
    seed_legacy_settings(session_factory, api_key="sk-plain")
    summary = migrate_settings(session_factory, cipher, apply=True)
    with session_factory() as session:
        row = session.get(UserSettings, 1)
        assert row.llm_api_key == ""
        assert "sk-plain" not in json.dumps(row.llm_configs_json)
        assert "sk-plain" not in json.dumps(row.llm_secrets_encrypted_json)
    assert summary.migrated == 1
```

- [ ] **Step 2: Run the focused tests and confirm missing schema/script failures**

Run: `python -m pytest backend/tests/test_credential_migration.py backend/tests/test_database_models.py -q`

Expected: FAIL because the encrypted column and migration function do not exist.

- [ ] **Step 3: Add the nullable JSON column and transactional migration command**

The Alembic upgrade adds `llm_secrets_encrypted_json JSON NULL`. The command assigns `config_id = uuid.uuid4().hex`, strips `api_key` and `embedding_api_key` from descriptors, encrypts the secret map, flushes ciphertext, blanks `llm_api_key`, and commits once per row. Dry-run performs no writes. `production_migrate.py` invokes Alembic programmatically, applies this migration with the configured keyring, then performs a read-only residual-plaintext count and fails unless it is zero.

- [ ] **Step 4: Add idempotency, malformed-row and wrong-key tests**

```python
def test_migration_is_idempotent(session_factory, cipher):
    first = migrate_settings(session_factory, cipher, apply=True)
    second = migrate_settings(session_factory, cipher, apply=True)
    assert first.migrated == 1
    assert second.migrated == 0
    assert second.already_secure == 1
```

- [ ] **Step 5: Run migration/model tests**

Run: `python -m pytest backend/tests/test_credential_migration.py backend/tests/test_database_models.py -q`

Expected: PASS.

- [ ] **Step 6: Commit schema and migration**

```bash
git add backend/migrations/versions/20260824_03_secure_llm_credentials.py backend/storage/models.py backend/scripts/migrate_llm_credentials.py backend/scripts/production_migrate.py backend/tests/test_credential_migration.py backend/tests/test_database_models.py
git commit -m "feat: migrate model keys out of plaintext columns"
```

### Task 3: Public and runtime settings repository contracts

**Files:**
- Modify: `backend/storage/learning_repository.py`
- Modify: `backend/tests/test_learning_storage.py`

**Interfaces:**
- Consumes: `CredentialCipher` injected into `LearningRepository`.
- Produces: `get_public_settings(user_id: int) -> dict` with `config_id`, `has_api_key`, `api_key_masked`.
- Produces: `get_runtime_settings(user_id: int) -> dict` containing decrypted secrets for internal calls only.
- Produces: `upsert_settings(user_id: int, configs: list[dict], active_index: int) -> dict` with blank-preserve and explicit-clear behavior.

- [ ] **Step 1: Replace plaintext expectations with failing public/runtime contract tests**

```python
def test_public_settings_mask_key_while_runtime_can_decrypt(repository):
    saved = repository.upsert_settings(1, [{"name": "A", "api_url": "https://api.example", "model_name": "m", "api_key": "sk-secret"}], 0)
    public = repository.get_public_settings(1)
    assert public["configs"][0]["has_api_key"] is True
    assert "api_key" not in public["configs"][0]
    assert repository.get_runtime_settings(1)["configs"][0]["api_key"] == "sk-secret"
    assert saved["configs"][0]["config_id"] == public["configs"][0]["config_id"]
```

- [ ] **Step 2: Run repository tests and confirm the old API leaks the key**

Run: `python -m pytest backend/tests/test_learning_storage.py -q`

Expected: FAIL on public masking and encrypted storage assertions.

- [ ] **Step 3: Implement descriptor/secret separation and strict `config_id` ownership**

Existing configs are indexed by server-owned `config_id`. For an existing ID, missing or empty secret fields preserve ciphertext; `clear_api_key is True` removes only that secret. Unknown client-supplied IDs raise `ValueError`; new configs receive server IDs.

- [ ] **Step 4: Add tests for reorder, blank preserve, explicit clear, deletion and foreign IDs**

```python
def test_blank_update_after_reorder_keeps_key_with_config_id(repository):
    first = repository.upsert_settings(1, [config("A", "key-a"), config("B", "key-b")], 0)
    reordered = [{**first["configs"][1], "api_key": ""}, {**first["configs"][0], "api_key": ""}]
    repository.upsert_settings(1, reordered, 0)
    runtime = repository.get_runtime_settings(1)["configs"]
    assert [item["api_key"] for item in runtime] == ["key-b", "key-a"]
```

- [ ] **Step 5: Run repository tests**

Run: `python -m pytest backend/tests/test_learning_storage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit repository contracts**

```bash
git add backend/storage/learning_repository.py backend/tests/test_learning_storage.py
git commit -m "feat: separate public and runtime model settings"
```

### Task 4: HTTP and frontend masked-settings compatibility

**Files:**
- Modify: `backend/api_v2.py`
- Modify: `backend/tests/test_learning_storage.py`
- Modify: `app/routes/home.tsx`
- Modify: `app/routes/auth.ts`
- Modify: `app/routes/auth-model.test.ts`
- Modify: `app/routes/home-auth-gate.test.tsx`

**Interfaces:**
- Consumes: repository public/runtime methods from Task 3.
- Produces: GET `/api/v2/settings` masked response and PUT blank-preserve request.

- [ ] **Step 1: Write failing API tests proving GET never returns a key and PUT can preserve it**

```python
def test_settings_get_masks_and_blank_put_preserves(authenticated_client):
    create_settings(authenticated_client, api_key="browser-must-not-see")
    response = authenticated_client.get("/api/v2/settings")
    body = response.get_json()
    assert "browser-must-not-see" not in response.get_data(as_text=True)
    body["configs"][0]["api_key"] = ""
    assert authenticated_client.put("/api/v2/settings", json=body).status_code == 200
```

- [ ] **Step 2: Run focused backend/frontend tests and confirm failures**

Run: `python -m pytest backend/tests/test_learning_storage.py -q`

Run: `npm test -- --run app/routes/auth-model.test.ts app/routes/home-auth-gate.test.tsx`

Expected: FAIL because the UI currently expects returned plaintext.

- [ ] **Step 3: Switch handlers to public/runtime methods and update frontend types**

Add optional `config_id`, `has_api_key`, and `api_key_masked` to the account profile type. Treat `has_api_key` as completeness for persisted profiles while keeping password inputs empty. PUT sends the stable ID and only newly typed secret values.

- [ ] **Step 4: Run focused backend/frontend tests and TypeScript compilation**

Run: `python -m pytest backend/tests/test_learning_storage.py -q`

Run: `npm test -- --run app/routes/auth-model.test.ts app/routes/home-auth-gate.test.tsx`

Run: `npx tsc --noEmit`

Expected: PASS.

- [ ] **Step 5: Commit API/frontend compatibility**

```bash
git add backend/api_v2.py backend/tests/test_learning_storage.py app/routes/home.tsx app/routes/auth.ts app/routes/auth-model.test.ts app/routes/home-auth-gate.test.tsx
git commit -m "fix: mask account model credentials"
```

### Task 5: Durable-output redaction and credential gate

**Files:**
- Create: `backend/storage/redaction.py`
- Create: `backend/tests/test_redaction.py`
- Modify: `backend/api_v2.py`
- Modify: `backend/tests/test_learning_storage.py`

**Interfaces:**
- Produces: `redact_sensitive(value: Any) -> Any` and `redact_text(text: object, secrets: Iterable[str] = ()) -> str`.
- Produces: startup/runtime rejection when Web rows retain plaintext or the keyring is unavailable.

- [ ] **Step 1: Add failing recursive redaction tests**

```python
def test_redactor_removes_nested_keys_and_signed_query_values():
    value = {"nested": {"api_key": "sk-x", "url": "https://x.test/a?Signature=abc&x=1"}}
    cleaned = redact_sensitive(value)
    assert cleaned["nested"]["api_key"] == "[REDACTED]"
    assert "abc" not in cleaned["nested"]["url"]
    assert "x=1" in cleaned["nested"]["url"]
```

- [ ] **Step 2: Run tests and confirm missing redactor failure**

Run: `python -m pytest backend/tests/test_redaction.py -q`

Expected: FAIL during import.

- [ ] **Step 3: Implement recursive key/query/text redaction and apply it to task/error/export boundaries**

The implementation must return copies, handle cycles defensively, preserve non-sensitive scalar types, and never include the original secret in raised errors.

- [ ] **Step 4: Run redaction, settings and API tests**

Run: `python -m pytest backend/tests/test_redaction.py backend/tests/test_learning_storage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the redaction boundary**

```bash
git add backend/storage/redaction.py backend/tests/test_redaction.py backend/api_v2.py backend/tests/test_learning_storage.py
git commit -m "fix: redact credentials from durable outputs"
```

### Task 6: Credential-security regression gate

**Files:**
- Modify only files required by failures caused by Tasks 1-5.

- [ ] **Step 1: Run all backend and frontend tests**

Run: `python -m pytest backend/tests -q`

Run: `npm test -- --run`

Run: `npx tsc --noEmit`

Expected: all commands PASS.

- [ ] **Step 2: Scan tracked runtime code for plaintext persistence patterns**

Run: `rg -n 'llm_api_key\s*=|"api_key"\s*:' backend/storage backend/api_v2.py`

Expected: matches are limited to legacy migration reads, runtime-only DTO construction and explicit redaction tests; no assignment writes plaintext to a mapped column or serialized job.

- [ ] **Step 3: Commit only necessary regression fixes**

```bash
git add backend app
git commit -m "test: close credential security regressions"
```
