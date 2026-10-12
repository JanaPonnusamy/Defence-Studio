# `data/` — included development database & artifacts

This directory is **intentionally tracked in Git** (an explicit allowlist
exception to the repo-wide `*.sqlite3` ignore rule) so a second development PC
can clone and continue from the same starting state.

## Contents

* `defence_studio.sqlite3` — the development database.
  * **Schema version:** 7
  * **Jobs:** 1 — `41714114-a85e-4ccf-a591-b254110c52d5`
  * **Status:** `AWAITING_REVIEW` (stages: application_foundation → extraction all
    `COMPLETED`; `evidence_analysis_and_review` = `AWAITING_REVIEW`; remainder
    `PENDING`)
  * **Owner:** `e2e-operator`
  * **Review decisions:** **0** — the evidence-review tables are empty on
    purpose; no decision has been submitted.
* `storage/jobs/<job_id>/` — the artifact bytes the job's records reference
  (discovery/acquisition/analysis JSON + the 3 acquired source `content.html`
  files). All checksums match the SHA-256 recorded in the database.

## Origin

Prepared on the originating machine via SQLite's backup API (WAL-safe) from the
end-to-end research database `E:\ds-e2e\e2e.sqlite3`, with the corresponding
artifact bytes copied from `E:\ds-e2e\store`. The original E2E database is
preserved unchanged on that machine as the recoverable source. The 49 **prepared
but unsubmitted** review decisions (`E:\ds-e2e\decision_manifest.json`) are
deliberately **not** included here and remain unsubmitted.

## Do not

* Do not submit review decisions just to "populate" the DB — the unreviewed
  state is the intended demonstration state.
* Do not develop against this tracked DB on two PCs at once. See the two-PC
  workflow in the top-level `README.md`.
