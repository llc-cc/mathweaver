# Task 5 brief — MySQL learning storage and complete job ownership

Implement only Task 5 in the writable mirror. This is a strict storage/ownership migration; do not implement the legacy SQLite importer (Task 6) or frontend work.

## Binding rules

- Work only in `D:\dev\Uniprism\uniprism_app\.mathweaver_work\pdfPipeline-main`; follow `AGENTS.md`.
- Strict TDD with a real RED run before production changes. Do not spawn subagents or initialize Git.
- Web mode uses SQLAlchemy learning storage exclusively for settings, history/task progress, and proof workspaces. It must not create, read, or update legacy `auth.db` for those features.
- Explicit desktop compatibility (`AI4MATH_DESKTOP=1` and no database URL) may retain the current SQLite branches.
- Repository methods require explicit `user_id` for every client-facing read/write. Do not return live ORM entities after a session closes.
- JSON conversion and datetime-to-ISO conversion belong at the repository boundary. Routes consume plain dictionaries/lists.
- Large PDFs, cache directories and ZIP/export material remain under the controlled `MATHGRAPH_DATA_DIR`; never store BLOBs or client-supplied absolute paths in MySQL.
- Web processing is authenticated. Anonymous `POST /api/v2/jobs` and `POST /api/v2/agent-import` return 401. Desktop legacy behavior may remain.
- Do not persist LLM API keys inside job/history snapshots or logs. User settings retain their existing user-scoped configuration contract.

## Files

- Create `backend/storage/learning_repository.py`
- Create `backend/tests/test_learning_storage.py`
- Modify `backend/api_v2.py` in settings/history/proof/job-resource paths
- Modify directly affected scripts/tests for the new Web persistence contract
- Modify models/migration only if an existing field is demonstrably insufficient; avoid speculative schema growth.

## Domain boundary

Define an immutable `JobSnapshot` that carries only persisted fields already represented by `History`:

```python
@dataclass(frozen=True)
class JobSnapshot:
    job_id: str
    filename: str
    status: str
    nodes: list[dict]
    edges: list[dict]
    source_markdown: str | None
    latex_macros: dict
    source_pdf: dict | None
    stage: str | None
    stage_label: str | None
    stage_index: int
    total_stages: int
    stages_done: list[str]
    source_format: str
    experimental_logic_ir: bool
    created_at: datetime
```

`source_pdf` must be sanitized before persistence to contain only status/public URLs/errors and safe basenames (`pdf_name`, `source_name`, `log_name`), never absolute paths.

Minimum repository behavior:

```python
class LearningRepository:
    def get_settings(self, user_id: int) -> dict: ...
    def upsert_settings(self, user_id: int, configs: list[dict], active_index: int) -> None: ...
    def list_history(self, user_id: int, limit: int = 50) -> list[dict]: ...
    def get_owned_history(self, user_id: int, history_id: str) -> dict | None: ...
    def upsert_job_progress(self, user_id: int, snapshot: JobSnapshot) -> bool: ...
    def update_source_pdf(self, user_id: int, history_id: str, safe_meta: dict) -> bool: ...
    def delete_owned_history(self, user_id: int, history_id: str) -> bool: ...
    def list_proof_workspaces(self, user_id: int, graph_id: str) -> list[dict]: ...
    def upsert_proof_workspace(self, user_id: int, graph_id: str, node_id: int, payload: dict) -> dict: ...
```

`upsert_job_progress` must never transfer ownership: if the primary-key job ID exists for another user, return false/raise a domain conflict and change nothing. Insert/update is one transaction.

Repository history dictionaries use native JSON values and ISO timestamp strings, with stable keys such as `nodes`, `edges`, `latex_macros`, `source_pdf`, and `stages_done`. Do not make routes call `json.loads` on repository data.

## Settings and proof workspaces

- Preserve the existing settings API JSON (`configs`, `active_index`) and active-config fallback columns.
- Validate settings body: JSON object, `configs` list, integer `active_index` (boolean is not an integer here), and index in range when configs is nonempty. Malformed input returns 400, not 500.
- `graph_id` length 1–64; `node_id` remains nonnegative integer from the route. Workspace fields normalize to existing response keys and lists.
- A second user cannot list or overwrite another user's proof workspace even with the same graph/node IDs.

## Job lifecycle persistence

- Authenticated Web job creation writes a `running` snapshot before starting the worker and sets `_history_persisted=True`.
- Agent import writes its `done` snapshot immediately and records `_user_id`.
- Existing event/pause/resume/error/done paths update the same owned history row.
- Running, paused, error and done status/stage/partial-or-final nodes/edges are queryable after `_jobs` is cleared while using the same database. A persisted running row remains a durable progress record; actual orphan-worker recovery policy is deferred to deployment startup work.
- History detail remains `409` unless status is done. History resume reconstructs the live job only for the owner and only with controlled artifact paths.
- Web-mode `_job_storage_root()` uses `MATHGRAPH_DATA_DIR/jobs`, not the legacy SQLite file location.

## Unified ownership helper

Create one helper used by every job-resource route. It must:

1. authenticate the request in Web mode;
2. check a live `_jobs` entry's `_user_id`;
3. otherwise load `get_owned_history(user_id, job_id)`;
4. return no resource for another owner (use stable 404 to avoid existence leakage);
5. return a plain live/persisted resource shape to the route.

Apply it to all of:

- job status;
- error detail;
- pause;
- cancel;
- resume;
- result;
- source PDF;
- compile log;
- locator;
- HTML export;
- artifact export.

For operations that require a live worker (pause/live resume), an owned persisted-only row returns a stable 409/410 rather than acting on another object. Desktop legacy branches may retain prior behavior.

Web artifact export must not use client-supplied fallback nodes/edges when no owned job/history exists. Persisted done results may be exported from database JSON. Artifact ZIP paths must be derived only from `_persistent_job_dir(job_id)` and existing controlled cache entries.

## Source PDF safety

- Reconstruct filesystem paths only as children of `_source_pdf_dir(job_id)` using stored basenames.
- Reject/ignore unsafe names and confirm resolved paths remain under the controlled job source-PDF directory before `send_file`.
- Background compile updates use the job's internal owner ID and repository; no unowned `UPDATE history WHERE id = ...` in Web mode.

## Required RED tests

- Settings persist across repository/app object replacement and remain isolated by user; malformed settings return 400.
- Proof workspaces persist and are isolated by user.
- Running, paused, error and done snapshots survive clearing `_jobs`; list/detail/status/result behavior is correct.
- Second user cannot access another user's status, error detail, result, source PDF, compile log, locator, HTML export or artifact export (404); cannot pause/cancel/resume it.
- Missing auth is 401 on Web create/import and every resource route.
- Anonymous Web job creation creates no artifact directory or database row.
- Job ID ownership cannot be overwritten by another user.
- DB source-PDF JSON contains no absolute path; traversal basenames cannot escape controlled roots.
- Web learning routes do not call `_get_db`, `sqlite3.connect`, or touch `_DB_PATH` (use a failing monkeypatch/sentinel).
- Progress upsert failure is surfaced and does not falsely mark `_history_persisted`.
- Existing pipeline/agent import/pause-resume regressions are updated to seed SQLAlchemy users and validate centralized storage.

## Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py tests/test_admin_authorization.py tests/test_admin_user_import.py tests/test_learning_storage.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
```

Run `py_compile` for all changed Python files.

## Report

Write `.superpowers/sdd/2026-08-21-web-auth-mysql/task-5-report.md` with RED/GREEN evidence, exact pass counts, files, SHA-256 for `storage/learning_repository.py` and `api_v2.py`, ownership-route checklist, self-review, and remaining filesystem/single-worker limitations.

