# Security review — public-deployment readiness

Date: 2026-06-11. Scope: things that matter if the app is exposed publicly.

## Findings

### 1. Hardcoded Neo4j credentials in client code — CRITICAL  ✅ code fixed / ⚠ must rotate
`app/config.ts` shipped a real Neo4j URI + username + password (`ai4math!`) inside
**client-side** source. Anything in `app/` is compiled into the browser bundle, so
those credentials were downloadable by any visitor, and the DB (`dev1.iezpark.com:8106`)
is reachable directly from the browser (`neo4j-driver` runs client-side in the legacy
`Neo4jQuery`/`useNeo4jQuery` code).

- **Fixed in code:** `config.ts` now reads `VITE_NEO4J_URI/USER/PASSWORD` from build-time
  env, defaulting to empty. No secret is bundled.
- **ACTION REQUIRED (cannot be fixed by code):** the password is in **git history** and
  on GitHub (`ss2599/pdfPipeline`). You must **rotate the Neo4j password** and, if the
  repo is/was public, treat `ai4math!` as compromised. Consider `git filter-repo` to
  purge it from history, but rotation is the real fix.
- **Design smell:** browsers should not connect to Neo4j directly with DB creds. If those
  graph features are revived, proxy them through the authenticated backend. These routes
  (`$territory`/`$uuid`/`$opid`) are **not registered in `routes.ts`**, so the main app is
  unaffected today.

### 2. Export endpoint has no auth / ownership check — MEDIUM  (recommend)
`backend/api_v2.py:export_html(job_id)` returns any finished job's full graph with no
`Authorization` check and no per-user ownership check. If job IDs are guessable/enumerable,
one user can download another's extracted content. Other endpoints use Bearer auth; this
one should too. (Left unchanged here to avoid altering backend behavior without you — see
recommendation below.)

### 3. Permissive CORS — LOW/MEDIUM  (recommend)
`backend/api_v2.py:47` uses `CORS(app)` (all origins). With Bearer-token auth the CSRF risk
is limited, but lock this to your known frontend origin(s) before going public.

### 4. KaTeX HTML injection surface — LOW (acceptable)
`MathText`/markdown render KaTeX via `dangerouslySetInnerHTML`, but only on KaTeX's own
`renderToString` output with `throwOnError:false` — KaTeX sanitizes its output, so this is
the standard, accepted pattern. No raw user HTML is injected elsewhere.

### 5. Dev fixture loader — LOW (gated)
`/workspace?fixture=NAME` is gated to `localhost`/`127.0.0.1` and only fetches
`/fixtures/<name>.json` with `name` restricted to `[\w-]`. Harmless in production (no such
files), but keep `public/fixtures/` out of production builds. Already gitignored.

## Recommended backend follow-ups (not applied automatically)
```python
# api_v2.py — require auth + ownership on export:
@app.route("/api/v2/export/<job_id>", methods=["POST"])
@require_auth                       # same decorator used by other v2 endpoints
def export_html(job_id):
    job = _jobs.get(job_id)
    if not job or job.get("user_id") != g.user_id:
        return jsonify({"error": "not found"}), 404
    ...

# lock CORS to known origins:
CORS(app, resources={r"/api/*": {"origins": ["https://your-frontend.example"]}})
```

## Verified clean
- `.env` / `backend/.env` are gitignored and untracked (API keys not committed).
- Studio fixtures with copyrighted book text are gitignored.
