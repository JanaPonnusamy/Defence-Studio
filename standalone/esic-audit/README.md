# ESIC Compliance Audit (standalone module)

A **one-time, user-initiated, read-only** audit of an employer's ESIC account. The employer
logs in to the official ESIC Employer Portal by hand. The module then reads the pages the
employer can access, reconciles contributions, interest, damages and recovery records with
deterministic arithmetic, and produces a PDF and an Excel report. Every audit is kept as an
immutable snapshot.

It lives under `standalone/` like the other Defence Era modules. It does not depend on, or
change, Defence Studio core. **Status: PARTIAL.** Built and fully tested against a
synthetic portal and has **not yet been verified on the real ESIC portal** (see Limitations).

## What it never does

It never generates or submits a challan, never pays, never edits employer or employee data,
never files or declares anything, and never changes a password. It does not bypass CAPTCHA,
OTP or MFA, and it does not call undocumented portal APIs. It never types into, reads or
submits the login form, and it stores no credentials. Details: [docs/SECURITY.md](docs/SECURITY.md).

## Architecture

```
ui/ (plain JS)  ->  app/main.py (FastAPI, 127.0.0.1, token)  ->  app/orchestrator.py (state machine, 1 worker thread)
                                                                   |-> app/browser.py   Playwright session + page inspector
                                                                   |-> app/safety.py    click guard, network guard, dialog guard
                                                                   |-> app/parsing.py   header-based table parsing (no positional guessing)
                                                                   |-> app/reconcile.py deterministic reconciliation (Decimal only)
                                                                   |-> app/rules/       statutory rules by effective date + sources
                                                                   |-> app/report_*.py  one model -> PDF (ReportLab) + Excel (openpyxl)
                                                                   '-> app/repository.py + db.py  SQLite, append-only, immutable snapshots
```

States: `READY → WAITING_FOR_LOGIN → AUTHENTICATED → INSPECTING → EXTRACTING → RECONCILING →
GENERATING_REPORTS → COMPLETED | PARTIAL`, plus `FAILED` and `CANCELLED`.

What came from Defence Era: the standalone-module layout (own venv, SQLite, `storage/`,
tests, docs, as in `standalone/audio-module`). Core has no Playwright, PDF or Excel
infrastructure, and its frontend is plain JS, so none of that could be reused. The module
adds these three libraries in its own venv only.

## Install

```powershell
cd "D:\jana soft\DefenceEra\standalone\esic-audit"
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
# Uses the installed Google Chrome (channel "chrome"). If Chrome is absent:
.venv\Scripts\python -m playwright install chromium
```

## Configuration (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `ESIC_AUDIT_STORAGE_DIR` | `storage/` | Database, evidence, reports, access token (git-ignored) |
| `ESIC_AUDIT_ACCESS_TOKEN` | generated → `storage/access_token.txt` | Token required by every `/api` call |
| `ESIC_AUDIT_BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge`, or empty for bundled Chromium |
| `ESIC_AUDIT_HEADLESS` | `0` | Must stay `0` for real audits (you need to see the window to log in) |
| `ESIC_AUDIT_LOGIN_URL` | official employer login page | Changing it marks every audit and report **SYNTHETIC**. For tests only. |
| `ESIC_AUDIT_PORT` | `8765` | Used by `scripts/run.ps1` |

The official login URL was verified on 2026-11-03:
`https://portal.esic.gov.in/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx`
(linked from esic.gov.in's "Employer Login"; `portal.esic.gov.in/` itself redirects to esic.gov.in).

## Run

```powershell
.\scripts\run.ps1        # binds to 127.0.0.1 only; prints where the access token is
```
Open `http://127.0.0.1:8765/` and paste the token once (kept for the browser tab only).

## Using it

1. **ESIC Compliance Audit → START ESIC AUDIT.** A Chrome window opens on the ESIC employer login page.
2. Log in yourself: username, password, CAPTCHA/OTP. Then press **I have logged in**. The module checks for the employer menu or a logout link before going on.
3. The module opens each page in `app/portal_map.json` (View Contribution History, Consolidated View Contribution History, Consolidated Monthly Contributions, Interest For Delay Payment, Recovery/Defaulter Challan, View RC). It reads the tables, follows result-grid pagination, and saves a screenshot and an HTML snapshot of each page.
4. Pages it cannot reach, or that need a month/year selection, are marked `NOT_FOUND` or show no data. Open them yourself in the portal window, pick a category (contribution, interest, **damages**, recovery…) and press **Capture current page**. No damages menu entry is known, so damages orders must be captured this way.
5. Press **Finish inspection**. The browser session is closed and destroyed, then the module extracts, reconciles and writes the reports.
6. **Audit History** reopens any earlier audit without going back to the portal. **Compare Audits** lists new demands, payments recorded, changed balances, new RCs, new periods and missing data.

An audit is **PARTIAL** when any of contribution, interest, damages or recovery could not be
read, or when the inspection was interrupted. Unread data is shown as **NOT AVAILABLE** and
never counted as zero. If the process stops mid-audit, the next start finalises that audit
from its saved page captures, with no portal access. Its status is at best PARTIAL.

## How figures are labelled

`CONFIRMED BY ESIC` (shown on an ESIC page) · `PAID` · `OUTSTANDING` (assessed − payments shown)
· `ESTIMATED` (computed here from a rule verified in an official text; never a demand) ·
`UNVERIFIED` (computed from a rule that could not be verified) · `NOT AVAILABLE`.

The total confirmed outstanding liability adds only distinct verified items. Interest or
damages already inside a recovery certificate (matched by demand reference or RC period)
are not added again. If an RC does not say what it covers, a `POSSIBLE_DOUBLE_COUNT`
discrepancy is raised. Estimated amounts are never added to confirmed amounts.

Rules, sources and verification status: [docs/LEGAL_RULES.md](docs/LEGAL_RULES.md).

## Database and migrations

SQLite at `storage/esic_audit.sqlite3`. Schema version 1 is applied automatically and
idempotently at startup (`app/db.py:migrate`). Future migrations must be appended to
`MIGRATIONS` and be additive only. Tables: `esic_audit_runs`, `esic_audit_stages`,
`esic_audit_events`, `esic_raw_captures`, `esic_contribution_records`,
`esic_interest_records`, `esic_damages_records`, `esic_recovery_records`,
`esic_audit_evidence`, `esic_audit_results`, `esic_audit_reports`. Database triggers make
record, evidence, report and result rows append-only, freeze audits in a terminal state,
and block deleting audit history. Indexes cover audit ID, contribution month, challan,
demand reference and RC reference. The module never touches Defence Studio's database.

## Tests

```powershell
.venv\Scripts\python -m pytest -q                 # everything (about 2 minutes; browser tests need Chrome/Edge)
.venv\Scripts\python -m pytest -q -m "not browser"  # fast unit/integration/security tests
.venv\Scripts\python scripts\simulated_audit.py     # full synthetic audit; prints sample PDF/XLSX paths
```
Results: [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## Deployment

The module is for a single employer, on a single machine. Run it on the employer's own
Windows PC, bound to `127.0.0.1`. Do not expose it on a network. `storage/` holds
confidential evidence and reports, so back it up as you would payroll records. Never
commit it (it is git-ignored). Upgrading is `git pull` plus
`pip install -r requirements.txt`. Migrations run at startup.

## Known limitations

* **The real ESIC portal pages after login have not been verified.** Menu labels come from the employer's screenshot. Column names come from a synonym list. The first real audit will show which stages report `NOT_FOUND` or `UNMAPPED`, and `app/portal_map.json` and `app/parsing.py` (FIELD_MAP) may need adjusting. Until then the status cannot be READY.
* No damages page is known. Damages need a manual capture. Otherwise the audit is PARTIAL and damages show as NOT AVAILABLE.
* Pages that need form input (month/year selectors) are not filled in automatically. The module never types into portal forms, so the employer selects and then presses Capture.
* The statutory basis after 21.11.2025 (Code on Social Security, 2020) is only partly verified. Estimates for delay on or after that date are UNVERIFIED. See LEGAL_RULES.md.
* Due-date extensions that ESIC granted for specific months (for example during COVID) are not modelled. Indicative interest for those months may be overstated, and ESIC's own figure always prevails.
* Parsing a PDF statement downloaded from the portal is not supported. Only on-screen tables are read.
