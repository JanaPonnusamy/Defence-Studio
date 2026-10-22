"""Opt-in production entry point for the existing durable research worker.

Run `python -m defence_studio.research_worker`. Committed research is assessed
through the existing deterministic policy service. Failed readiness stops
progression; no script-provider call is scheduled here.
"""
import signal
import uuid

from defence_studio.acquisition.handler import build_acquisition_handler
from defence_studio.config import database_path, storage_root
from defence_studio.database import initialize_database
from defence_studio.discovery.handler import build_discovery_handler
from defence_studio.services.analysis import make_analysis_handler
from defence_studio.services.worker import Worker
from defence_studio.storage.artifacts import ArtifactStore


def build_research_worker(database_file, storage_path, *, discovery_handler=None, acquisition_handler=None, assessment_service=None):
    store = ArtifactStore(storage_path)

    def foundation(context, cancel):
        # Job creation already persisted identity, all stages and owner. This
        # handler merely acknowledges that initialized foundation in its claim.
        if not context.job_id or not context.owner_id or cancel.is_set():
            raise RuntimeError("Foundation context is unavailable or cancelled")
        return []

    handlers = {
        "application_foundation": foundation,
        "news_feed_and_topic_discovery": discovery_handler or build_discovery_handler(store),
        "topic_search_and_source_collection": acquisition_handler or build_acquisition_handler(store),
        "extraction_validation_normalization_deduplication": make_analysis_handler(database_file, storage_path),
    }
    from defence_studio.services.voice import VoiceService
    handlers.update(VoiceService(database_file, storage_path).handlers())
    def assess_completed(context):
        if context.stage_key == "extraction_validation_normalization_deduplication":
            from defence_studio.services.evidence_policy import EvidencePolicyService
            (assessment_service or EvidencePolicyService(database_file, storage_path)).run_assessment(context.job_id, context.owner_id)

    return Worker(database_file, f"research-{uuid.uuid4()}", handlers, retry_safe_stages=set(handlers), after_stage=assess_completed)


def main():
    db = database_path()
    initialize_database(db)
    worker = build_research_worker(db, storage_root())
    from contextlib import closing
    from defence_studio.database import connect
    from defence_studio.services.evidence_policy import EvidencePolicyService
    from defence_studio.services.review import ReviewValidationError
    with closing(connect(db)) as connection:
        pending = connection.execute("SELECT job_id,owner_id FROM workflow_jobs WHERE status='AWAITING_REVIEW'").fetchall()
    policy = EvidencePolicyService(db, storage_root())
    for row in pending:
        latest = policy.latest(row["job_id"], row["owner_id"])
        try:
            inputs = policy._load(row["job_id"], row["owner_id"])
        except ReviewValidationError:
            policy.run_assessment(row["job_id"], row["owner_id"])
            continue
        if (not latest or latest.get("is_stale") or latest.get("analysis_sha256") != inputs[2]["sha256"] or
                latest.get("acquisition_sha256") != inputs[1]["sha256"] or
                latest.get("configuration_sha256") != policy.configuration_sha256 or
                latest.get("registry_sha256") != policy.registry_sha256):
            policy.run_assessment(row["job_id"], row["owner_id"])
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: worker.shutdown())
    worker.run_forever()


if __name__ == "__main__":
    main()
