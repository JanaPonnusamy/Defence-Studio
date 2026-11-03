# Test report — 2026-11-03

Environment: Windows 11, Python 3.13.15, module venv (`standalone/esic-audit/.venv`), Google Chrome via the Playwright `chrome` channel, headless.

Command: `.venv\Scripts\python -m pytest -q` → **114 passed, 0 failed, 0 skipped** (36 s). One third-party deprecation warning comes from Starlette's TestClient.

| File | Tests | Covers |
|---|---|---|
| test_esic_money_rules.py | 33 | Indian-format money parsing, blank → None (never 0), invalid → error, Decimal only, wage-month and date parsing, due dates by effective date (21 → 15 days, post-CoSS unverified), per-day interest, interest split across rule boundaries (2005 and 2025), damages slabs, s.85B cap, sources on every rule |
| test_esic_parsing.py | 7 | Header-name mapping (column order irrelevant), pager/total/"no records" rows skipped, unmapped tables reported rather than guessed, original values kept, stable duplicate keys, employer-code detection |
| test_esic_reconciliation.py | 17 | Expected confirmed totals, period classification, challan ≠ payment, potential damages only where unassessed, missing periods, NOT AVAILABLE ≠ 0, EMPTY page = verified nil, duplicate and inconsistent challans, assessed − paid with mismatch flag, unknown payments, proposed damages pending, negative balances, RC linkage by reference and period with no double counting, undetermined overlap flagged, ESIC vs indicative interest, determinism |
| test_esic_persistence_reports.py | 10 | Decimal stored as TEXT, duplicate prevention, append-only triggers, terminal audits frozen, history undeletable, per-audit isolation, redaction, Excel 12 sheets / audit ID / filters / frozen headers / SUBTOTAL formulas, PDF ↔ Excel figures reconcile exactly, detail rows add up to summary, NOT AVAILABLE rendered |
| test_esic_security.py | 44 | Every forbidden label refused, view labels allowed, ASP.NET postback classification, payment hosts blocked even during login, writes and navigation off the portal blocked, login POST never parsed, no fill/type/submit or persistent profile in the module source, no credential or payment routes, API token required, masking and log redaction |
| test_esic_browser_e2e.py | 3 | Real Chrome against the synthetic portal: full audit through the API (login handoff, guards, postback menu, pagination, assisted damages capture, evidence hashes, PDF/XLSX download, second PARTIAL audit leaves the first unchanged, comparison); cancel while waiting for login; expired session never yields a zero liability |

Security observations from the end-to-end run (synthetic portal): Generate Challan click blocked; raw `btnPayNow` POST blocked; "Pay online" link blocked; SBI gateway navigation blocked; fixture server state-changing requests = **0**; the typed fake password was found in **no** file under `storage/`.

Simulated audit (`scripts/simulated_audit.py`): COMPLETED, 0 forbidden hits, PDF (15 pages) and XLSX produced. Pages were rendered and checked visually. The UI was checked at 1440 px and 390 px (no JS errors, no horizontal overflow, Excel download works).

**Not tested:** the real ESIC portal after login. That needs the employer's own manual login and has not been done.

Defence Studio core: not modified, so its suites were not re-run.
