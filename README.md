# Defence Studio

A framework-free FastAPI + static-ES-module content-intelligence workspace for
source-grounded research, evidence review, and script/scene production. This
repository ships with a **checked-in development database** holding one real
research job in the `AWAITING_REVIEW` state so a freshly-cloned machine can open
the UI and continue work immediately.

> Authoritative design: `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` ·
> Current state: `project_status.md`

---

## What's included for local development

| Path | Purpose |
|---|---|
| `data/defence_studio.sqlite3` | The development database (schema v7, one job `4171…10c52d5`, `AWAITING_REVIEW`, **zero** submitted review decisions). |
| `data/storage/jobs/<job_id>/` | The artifact bytes that job's records reference (discovery/acquisition/analysis JSON + 3 source `content.html`). |
| `.env.template` | Template for your machine-local `backend/.env.local` (secrets live only there, never committed). |
| `scripts/run_dev.ps1` / `scripts/run_dev.sh` | Launch the API + static UI on `http://127.0.0.1:8011`. |
| `backend/scripts/db_tool.py` | `validate` / `snapshot` / `fingerprint` the DB for the two-PC workflow. |
| `backend/scripts/mint_dev_token.py` | Mint a production-style bearer token locally (alternative to the dev bypass). |

The 49 **prepared** review decisions (3 sources + 46 claims) are intentionally
**not** committed — they remain unsubmitted and live only in the external
`E:\ds-e2e\decision_manifest.json` on the originating machine.

---

## First-time setup (Windows)

```powershell
# 1. Clone
git clone https://github.com/JanaPonnusamy/Defence-Studio.git
cd Defence-Studio

# 2. Create a virtualenv and install dependencies
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\python.exe -m pip install -e backend[test]

# 3. Create your machine-local environment file
Copy-Item .env.template backend\.env.local
#   then edit backend\.env.local and set DEFENCE_STUDIO_AUTH_SECRET to a random
#   >=32-byte value, e.g.:
backend\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"

# 4. (Optional) enable the local dev-auth bypass by uncommenting these in
#    backend\.env.local:
#       DEFENCE_STUDIO_DEV_MODE=1
#       DEFENCE_STUDIO_DEV_AUTH_BYPASS=1

# 5. Validate the included database before first run
backend\.venv\Scripts\python.exe backend\scripts\db_tool.py validate

# 6. Launch
powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1
```

Open `http://127.0.0.1:8011/`. With the dev bypass enabled, click **Enter
development mode** on the login screen — the server mints a short-lived token
for you (no copy/paste). The **Dashboard** lists the included job; open it to
review its sources and claims.

> Git Bash users: use `scripts/run_dev.sh` instead of the `.ps1`.

### Authentication modes

* **Production (default).** Every `/api/...` route requires a valid HS256 bearer
  token signed with `DEFENCE_STUDIO_AUTH_SECRET`, issued by your trusted issuer.
* **Development bypass (opt-in, local only).** With **both**
  `DEFENCE_STUDIO_DEV_MODE=1` and `DEFENCE_STUDIO_DEV_AUTH_BYPASS=1`, a **direct
  loopback** request (127.0.0.1/::1, no proxy headers) is accepted as the single
  configured `DEFENCE_STUDIO_DEV_AUTH_OWNER` without a token, and
  `GET /api/dev/session` mints a token for the UI. It **fails closed** for any
  non-loopback origin or proxied request, never selects an owner from request
  input (so ownership isolation holds), and must never be enabled in production.

---

## Running tests

```powershell
# Backend
cd backend
.venv\Scripts\python.exe -m pytest -q

# Frontend (Node 18+)
cd ..\frontend
node --test tests\api.test.mjs tests\dashboard.test.mjs
```

---

## Two-PC database workflow

The SQLite database is a **binary** file tracked in Git. SQLite files **cannot
be merged as text**, so coordinate so only one PC changes the tracked DB at a
time. Treat the DB like a shared document, not a branchable source file.

**Before you start work on a PC**

1. Stop the app if it is running.
2. `git pull` to get the latest source **and** database.
3. If the pull reports a conflict on `data/defence_studio.sqlite3`, **stop** —
   do not attempt a text merge. Back up your local copy
   (`backend\.venv\Scripts\python.exe backend\scripts\db_tool.py snapshot data\local-backup.sqlite3`)
   and resolve by choosing one side deliberately.
4. `db_tool.py validate` to confirm integrity and the expected job state.

**Before you commit a changed database**

1. **Stop the app** (so no WAL writes are in flight).
2. Make a consistent, single-file snapshot over the live DB in place:
   `backend\.venv\Scripts\python.exe backend\scripts\db_tool.py snapshot data\defence_studio.sqlite3 --force`
   (the backup API is WAL-safe; this also truncates any WAL side-files).
3. `db_tool.py validate` again.
4. Commit the DB **in its own commit** (or clearly separated from source
   changes) so database updates are easy to identify and revert.

**Detecting divergence before pulling a changed DB**

Compare content fingerprints to know whether your local DB differs from the one
you're about to pull:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\db_tool.py fingerprint      # your local DB
# ... after fetching, check out the incoming file to a temp path and fingerprint it too
```

Different fingerprints mean the DBs diverge — **never blindly overwrite a newer
local DB**; snapshot your local copy first, then decide which to keep.

**Rules**

* Don't develop against the tracked DB on both PCs simultaneously.
* Never force-push or rewrite history to resolve a DB conflict.
* Keep secrets (`backend/.env.local`), caches, venvs, and logs out of Git.
