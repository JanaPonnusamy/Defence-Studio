"""Opt-in production entry point for the existing durable research worker.

Run `python -m defence_studio.research_worker`. Stops at the human review
boundary: no script, voice, rendering or publication handlers are registered.
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


def build_research_worker(database_file, storage_path, *, discovery_handler=None, acquisition_handler=None):
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
    return Worker(database_file, f"research-{uuid.uuid4()}", handlers, retry_safe_stages=set(handlers))


def main():
    db = database_path()
    initialize_database(db)
    worker = build_research_worker(db, storage_root())
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: worker.shutdown())
    worker.run_forever()


if __name__ == "__main__":
    main()
