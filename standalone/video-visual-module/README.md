# Video Visual / Scene Composition Module (standalone)

Converts narration plus a library of visual assets into a **timed, renderer-neutral visual shot plan**. It decides which visual to show, when and for how long, whether it is source material or AI-generated, how it should move or crop, and what attribution it needs. It does **not** render video (spec §0, §35).

It is fully standalone, with its own source tree, config (`app/config.py`, `.env`), SQLite DB plus Alembic migrations, `storage/` folder, in-process job queue, API, UI, tests and docs. It does not import from or depend on any parent application or sibling module.

## Quick start (Windows PowerShell 5.1)

```powershell
cd "D:\Jana Soft\defenceStudio\standalone\video-visual-module"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\serve.py          # API + UI on http://127.0.0.1:8765
```

- UI: <http://127.0.0.1:8765/ui/> (deep link: `/ui/?project=<id>&tab=planner`)
- OpenAPI docs: <http://127.0.0.1:8765/docs> · Health: `/health`
- Alternative launch: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765`

The database is created and migrated automatically on startup (`alembic upgrade head`). Interrupted jobs are recovered at startup.

## Tests and self-test

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt      # adds Playwright (dev only) on top of requirements.txt
.\.venv\Scripts\python.exe -m pytest                                   # 180 tests: 178 pass, 2 opt-in paid tests skipped, ~26 s
powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1   # end-to-end, exit 0 = all required checks passed (33)
.\.venv\Scripts\python.exe scripts\benchmark_10min.py                  # measured 10-minute planning benchmark
.\.venv\Scripts\python.exe scripts\ui_browser_check.py                 # automated headless-Edge UI check -> docs\ui_screenshots
```

## Image providers (v1.1)

- **`placeholder-procedural`** is the default and needs no network. It makes PLACEHOLDER images (procedural, **not AI, never evidence**) with no cost. The old name `local-procedural` still works.
- **`openai-images`** is an optional real AI provider and is billable. It is used only when `OPENAI_API_KEY` is set; otherwise it reports `NOT_CONFIGURED` and makes no request.
- A failed real-AI request never silently turns into a placeholder. Fallback happens only with `allow_placeholder_fallback`, and the result is labelled FALLBACK.
- Costs are `KNOWN` (documented pricing × provider usage), `UNKNOWN` or `NOT_APPLICABLE`. Nothing is invented.
- See `docs/PROVIDER_SETUP.md`.

## Typical workflow (API)

1. `POST /projects` → `project_id`.
2. Import assets: `POST /assets/import` (multipart; provenance, licence and rights). Render first-class data graphics from structured data with `POST /assets/stats` and `POST /assets/maps`.
3. `POST /scenes` with narration text (paragraph = scene, sentence = narration segment). You can instead send timed `segments` from an external timing contract.
4. `POST /visual-plans/build` → job, then poll `GET /jobs/{id}` until `SUCCEEDED`. Optionally set `generate_ai: true` to create at most 4 illustrative hook images through the provider-neutral adapter. With the default provider these are labelled PLACEHOLDER images, not AI.
5. `GET /visual-plans/{id}/timeline` (renderer contract) or `GET /shots/{plan_id}`. Previews come from `/shots/{plan}/{shot}/preview` and `/visual-plans/{id}/contact-sheet`.
6. `GET /costs/{project_id}` for the episode cost report.

## Docs

| File | Content |
|---|---|
| `docs/ARCHITECTURE.md` | Components, pipeline, data model, storage layout, editorial rules |
| `docs/API_CONTRACTS.md` | Endpoints, schemas, IDs, statuses, error format, timeline contract, idempotency |
| `docs/RUNBOOK.md` | Operating, configuration, troubleshooting, recovery, backup |
| `docs/KNOWN_LIMITATIONS.md` | What this build does not do |
| `docs/SELF_TEST_COMMANDS.ps1` | Self-test script (PowerShell 5.1) |
| `docs/SELF_TEST_REPORT.md` | Results of the actual self-test run and measured benchmark numbers |
| `docs/PROVIDER_SETUP.md` | Image providers, OpenAI setup, states, fallback, cost rules and pricing sources, opt-in live tests |
| `docs/PHASE_2_REPORT.md` | Phase 2 changes, baseline vs final results, changed files, backups, reproduce commands |
| `docs/ui_screenshots/` | Automated browser verification screenshots and report |

## Layout

```
app/            FastAPI app: api/ (routers), db/ (models, init), jobs/ (queue, handlers),
                pipeline/ (import, scene parser, intent, selector, motion, shot planner, timeline,
                graphics, preview), generation/ (adapter contract, local + mock providers, service)
alembic/        this module's own migrations
ui/             plain HTML/CSS/JS UI served at /ui
fixtures/       synthetic, locally generated test data (fictional 10-minute episode)
scripts/        serve.py, benchmark_10min.py
tests/          pytest suite
storage/        module-owned runtime data (created on first run, git-ignored)
```
