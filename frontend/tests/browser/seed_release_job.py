"""Seed a disposable Defence Studio database for frontend release acceptance.

Uses existing job, artifact, and analysis services. Does not modify product
source, production databases, or review history outside the disposable files
named by DEFENCE_STUDIO_DB_PATH and DEFENCE_STUDIO_STORAGE_PATH.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from defence_studio.contracts.acquisition import ACQUISITION_RESULT_SCHEMA
from defence_studio.contracts.analysis import ACQUISITION_RESULT_ARTIFACT
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import initialize_database
from defence_studio.services.analysis import AnalysisService
from defence_studio.services.jobs import JobService
from defence_studio.storage.artifacts import ArtifactStore

OWNER = os.environ.get("DEFENCE_STUDIO_DEV_AUTH_OWNER", "e2e-operator")
TOPIC = "Synthetic Northwind harbour drill"
SRC_A = (
    "The Northwind base counted 4 patrol craft during the March drill.\n"
    "The drill took place on 2024-03-01 according to the harbour notice.\n"
)
SRC_B = (
    "A harbour notice said the Northwind March drill used 4 patrol craft.\n"
    "The notice dated the drill to 2024-03-01.\n"
)


def _source(store, job_id, source_id, url, text):
    stored = store.write_bytes(job_id, f"jobs/{job_id}/sources/{source_id}.bin", text.encode("utf-8"), "text/plain")
    return {
        "source_id": source_id, "target_id": source_id, "status": "ACQUIRED",
        "method": "http_page", "source_type": "article",
        "requested_url": url, "canonical_url": url, "final_url": url,
        "acquisition_timestamp": "2026-10-15T00:00:00.000000+00:00",
        "declared_metadata": {
            "title": f"Synthetic source {source_id}", "title_status": "DECLARED",
            "publisher": "Synthetic Harbour Notice", "publisher_status": "DECLARED",
            "published_at": "2024-03-01", "published_at_status": "DECLARED",
            "language": "en", "language_status": "DECLARED",
        },
        "artifacts": [{
            "artifact_type": "source_content", "uri": stored.uri, "sha256": stored.sha256,
            "bytes": stored.bytes, "media_type": "text/plain",
            "schema_version": "defence-studio.source-artifact.v1",
        }],
        "original_metadata": {}, "provenance": None, "warnings": [], "error": None,
    }


def main() -> int:
    db = Path(os.environ["DEFENCE_STUDIO_DB_PATH"])
    root = Path(os.environ["DEFENCE_STUDIO_STORAGE_PATH"])
    db.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    initialize_database(db)
    jobs = JobService(db)
    store = ArtifactStore(root)
    populated = jobs.create(OWNER, "cursor-release-populated", TOPIC, "en")["job_id"]
    empty = jobs.create(OWNER, "cursor-release-empty", "Empty acceptance job", "en")["job_id"]
    for stage in JOB_STAGES[:2]:
        jobs.transition(populated, OWNER, stage, "SKIPPED")
    acq, ana = JOB_STAGES[2], JOB_STAGES[3]
    jobs.transition(populated, OWNER, acq, "RUNNING")
    sources = [
        _source(store, populated, "src-a", "https://example.com/northwind-a", SRC_A),
        _source(store, populated, "src-b", "https://example.com/northwind-b", SRC_B),
    ]
    payload = {
        "job_id": populated, "acquired_at": "2026-10-15T00:00:00.000000+00:00", "sources": sources,
        "requested": 2, "succeeded": 2, "failed": 0, "skipped_duplicate": 0, "warnings": [],
        "schema_version": ACQUISITION_RESULT_SCHEMA,
    }
    stored = store.write_bytes(
        populated, f"jobs/{populated}/research/acquisition_result.json",
        json.dumps(payload).encode("utf-8"), "application/json")
    jobs.add_artifact(populated, OWNER, acq, ACQUISITION_RESULT_ARTIFACT, stored.uri, stored.sha256,
                      "defence-studio.acquisition-result.v1")
    jobs.transition(populated, OWNER, acq, "COMPLETED")
    jobs.transition(populated, OWNER, ana, "RUNNING")
    ref, _ = AnalysisService(db, root).run_and_store(populated, OWNER, stored.uri, stored.sha256, topic=TOPIC)
    jobs.add_artifact(populated, OWNER, ana, ref["artifact_type"], ref["uri"], ref["sha256"], ref["schema_version"])
    jobs.transition(populated, OWNER, ana, "COMPLETED")
    print(json.dumps({"owner": OWNER, "populated_job": populated, "empty_job": empty, "db": str(db)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
