"""Reanalyse only this run's new, unreviewed job; retain all previous artifacts."""
import collections
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))
from defence_studio.services.analysis import AnalysisService, ANALYSIS_STAGE
from defence_studio.services.jobs import JobService
from defence_studio.services.review import ReviewService
from defence_studio.services.evidence_policy import EvidencePolicyService
from defence_studio.services.evidence import EvidenceService
from defence_studio.storage.artifacts import ArtifactStore

job_id, owner = "ff16816c-7af1-477a-b5f7-888ebd42f21a", "e2e-operator"
db, root = REPO / "data/defence_studio.sqlite3", REPO / "data/storage"
jobs, review, store = JobService(db), ReviewService(db, root), ArtifactStore(root)
job = jobs.get(job_id, owner)
assert job["status"] == "AWAITING_REVIEW"
before = review.list_claims(job_id, owner)["claims"]
assert all(c["review"] is None for c in before)
assert all(s["review"] is None for s in review.list_sources(job_id, owner)["sources"])
old_artifacts = {a["uri"]: a["sha256"] for a in job["artifacts"]}
acquisition = max((a for a in job["artifacts"] if a["artifact_type"] == "acquisition_manifest"),
                  key=lambda a: a["version"])
ref, result = AnalysisService(db, root).run_and_store(
    job_id, owner, acquisition["uri"], acquisition["sha256"], topic=job["topic"])
if not any(a["uri"] == ref["uri"] and a["sha256"] == ref["sha256"] for a in job["artifacts"]):
    jobs.add_artifact(job_id, owner, ANALYSIS_STAGE, ref["artifact_type"], ref["uri"],
                      ref["sha256"], ref["schema_version"])
policy = EvidencePolicyService(db, root).run_assessment(job_id, owner)
after = review.list_claims(job_id, owner)["claims"]
assert all(store.sha256_of(job_id, uri) == sha for uri, sha in old_artifacts.items())
assert all(c["review"] is None for c in after)
counts = collections.Counter((c["source_id"], c["claim_text"]) for c in after)
assert all(n == 1 for n in counts.values())
report = {"job_id": job_id, "topic": job["topic"], "claims_before": len(before),
          "claims_after": len(after), "duplicate_claims_after": 0,
          "normalizer_version": result.versions.normalization_version,
          "all_prior_artifacts_preserved": True, "human_decisions_added": 0,
          "status": jobs.get(job_id, owner)["status"],
          "assessment_outcomes": dict(collections.Counter((c.get("assessment") or {}).get("outcome") for c in after)),
          "readiness": policy["ready_for_generation"], "blockers": policy["policy_criteria_failed"]}
Path(__file__).with_name("job-reanalysis.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
