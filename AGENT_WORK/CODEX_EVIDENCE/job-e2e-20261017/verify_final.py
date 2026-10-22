"""Read-only final API, artifact, and original-row preservation checks."""
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import httpx

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
sys.path.insert(0, str(REPO / "backend"))
from defence_studio.auth import mint_dev_session_token

JOB = "ff16816c-7af1-477a-b5f7-888ebd42f21a"
BASE = "http://127.0.0.1:8011/api"
checks = {}
with httpx.Client(timeout=30) as client:
    session = client.get(BASE + "/dev/session").json()
    headers = {"Authorization": "Bearer " + session["token"]}
    states = {}
    for label, suffix in [("job", ""), ("monitor", "/monitor"), ("claims", "/review/claims"),
                          ("sources", "/review/sources"), ("policy", "/evidence/policy"),
                          ("package", "/package"), ("scripts", "/script/revisions")]:
        response = client.get(BASE + "/jobs/" + JOB + suffix, headers=headers)
        response.raise_for_status()
        states[label] = response.json()
        (OUT / (label + "-final.json")).write_text(json.dumps(response.json(), indent=2))
    checks["review_gate_persisted_after_restart"] = states["job"]["status"] == "AWAITING_REVIEW"
    checks["research_stages_complete"] = all(s["status"] == "COMPLETED" for s in states["job"]["stages"][:4])
    checks["downstream_stages_pending"] = all(s["status"] == "PENDING" for s in states["job"]["stages"][5:])
    checks["two_real_sources_acquired"] = len(states["sources"]["sources"]) == 2 and all(
        s["status"] == "ACQUIRED" for s in states["sources"]["sources"])
    claims = states["claims"]["claims"]
    checks["27_claims_no_hidden_duplicates"] = len(claims) == 27 and len({
        (c["source_id"], c["claim_text"]) for c in claims}) == 27
    checks["assessment_current"] = not states["policy"]["is_stale"]
    checks["no_false_eta"] = states["monitor"]["estimate"]["remaining_seconds"] is None
    checks["no_human_reviews_added"] = all(c["review"] is None for c in claims)
    checks["independence_gate_enforced"] = not states["policy"]["ready_for_generation"]
    assert checks["independence_gate_enforced"]  # negative gate test only
    response = client.post(BASE + "/jobs/" + JOB + "/script/generate",
        headers={**headers, "Idempotency-Key": "e2e-readiness-denial-20261017"},
        json={"language": "en", "audience": "general", "tone": "neutral",
              "script_format": "narration", "target_duration_seconds": 120,
              "target_words": 150, "model": "offline-extractive",
              "prompt_version": "defence-studio.source-grounded-script.v1", "max_text_cost_inr": 0.01})
    checks["script_generation_blocked_by_api"] = response.status_code == 409
    (OUT / "script-gate-final.json").write_text(json.dumps({"status": response.status_code, "body": response.json()}, indent=2))
    checks["no_script_revision_created"] = not client.get(BASE + "/jobs/" + JOB + "/script/revisions",
                                                          headers=headers).json()["revisions"]
    checks["invalid_bearer_rejected"] = client.get(BASE + "/jobs/" + JOB,
        headers={"Authorization": "Bearer invalid"}).status_code == 401
    checks["proxied_anonymous_rejected"] = client.get(BASE + "/jobs/" + JOB,
        headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 401
    config = dict(line.split("=", 1) for line in (REPO / "backend/.env.local").read_text().splitlines()
                  if line and not line.lstrip().startswith("#") and "=" in line)
    other = mint_dev_session_token(config["DEFENCE_STUDIO_AUTH_SECRET"].strip(), "e2e-other-owner")
    checks["cross_owner_rejected"] = client.get(BASE + "/jobs/" + JOB,
        headers={"Authorization": "Bearer " + other}).status_code == 404

before = sqlite3.connect((REPO / "tmp/job-e2e-20261017/before.sqlite3").as_uri() + "?mode=ro", uri=True)
after = sqlite3.connect((REPO / "data/defence_studio.sqlite3").as_uri() + "?mode=ro", uri=True)
changed = []
for (table,) in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
    name = '"' + table.replace('"', '""') + '"'
    missing = collections.Counter(before.execute("SELECT * FROM " + name)) - collections.Counter(after.execute("SELECT * FROM " + name))
    if missing:
        changed.append({"table": table, "changed_or_missing_rows": sum(missing.values())})
checks["every_original_database_row_preserved"] = not changed
checks["exactly_one_new_job"] = after.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == before.execute(
    "SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] + 1
checks["database_integrity"] = after.execute("PRAGMA quick_check").fetchone()[0] == "ok"
prior = json.loads((OUT / "preservation-before.json").read_text())
checks["35_original_artifact_files_preserved"] = all(
    hashlib.sha256((REPO / p).read_bytes()).hexdigest() == sha for p, sha in prior["artifacts"].items())
checks["new_job_artifact_checksums_valid"] = all(
    hashlib.sha256((REPO / "data/storage" / a["uri"]).read_bytes()).hexdigest() == a["sha256"]
    for a in states["job"]["artifacts"])
report = {"job_id": JOB, "checks": checks, "passed": sum(checks.values()), "total": len(checks),
          "original_row_changes": changed, "status": states["job"]["status"],
          "claim_outcomes": dict(collections.Counter((c.get("assessment") or {}).get("outcome") for c in claims)),
          "blockers": states["policy"]["policy_criteria_failed"]}
(OUT / "verification-final.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
assert all(checks.values()), report
