"""Durable-worker stage handler for F3 content acquisition.

Binds the acquisition service to the locked `topic_search_and_source_collection`
stage so acquisition runs only through the verified F2 worker — never
synchronously in an HTTP request and never through a side queue.

The handler is retry-safe: it reads the approved request artifact produced by a
completed predecessor stage, verifies the request bytes against the reference
hash, runs acquisition (which reuses already-stored, hash-verified sources
instead of re-downloading), writes a single hash-addressed result manifest, and
returns exactly that manifest reference for the worker to persist in one
transaction. A missing or corrupt request fails closed rather than inventing a
default.
"""

from __future__ import annotations

import json
import threading

from defence_studio.acquisition.service import AcquisitionService
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.contracts.acquisition import (
    ACQUISITION_RESULT_SCHEMA,
    RequestValidationError,
    request_from_dict,
    result_to_dict,
)
from defence_studio.services.worker import StageContext
from defence_studio.storage.artifacts import ArtifactStore, StoragePathError

ACQUISITION_REQUEST_ARTIFACT_TYPE = "acquisition_request"
ACQUISITION_MANIFEST_ARTIFACT_TYPE = "acquisition_manifest"
ACQUISITION_MANIFEST_URI_SUFFIX = "research/acquisition_result.json"


class AcquisitionHandlerError(Exception):
    """Fail-closed condition that should surface as a stage failure."""


def build_acquisition_handler(store: ArtifactStore, registry=None, guard: UrlGuard | None = None,
                              client_factory=None):
    service = AcquisitionService(store=store, guard=guard, registry=registry,
                                 client_factory=client_factory)

    def handle(context: StageContext, cancel: threading.Event):
        request_ref = _find_request(context)
        if request_ref is None:
            raise AcquisitionHandlerError(
                "No approved acquisition_request artifact was provided to this stage"
            )
        uri = request_ref["uri"]
        try:
            if store.sha256_of(context.job_id, uri) != request_ref["sha256"]:
                raise AcquisitionHandlerError("Acquisition request bytes do not match the reference hash")
            payload = json.loads(store.read_text(context.job_id, uri))
        except StoragePathError as exc:
            raise AcquisitionHandlerError(f"Acquisition request path is unsafe: {exc}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise AcquisitionHandlerError(f"Acquisition request could not be read: {exc}") from exc

        try:
            request = request_from_dict(payload)
        except RequestValidationError as exc:
            raise AcquisitionHandlerError(str(exc)) from exc
        if request.job_id != context.job_id:
            raise AcquisitionHandlerError("Acquisition request job_id does not match the stage job")

        result = service.run(request, cancel=cancel)

        manifest_uri = f"jobs/{context.job_id}/{ACQUISITION_MANIFEST_URI_SUFFIX}"
        data = json.dumps(result_to_dict(result), indent=2, sort_keys=True).encode("utf-8")
        stored = store.write_bytes(context.job_id, manifest_uri, data, "application/json")
        return [{
            "artifact_type": ACQUISITION_MANIFEST_ARTIFACT_TYPE,
            "uri": stored.uri,
            "sha256": stored.sha256,
            "schema_version": ACQUISITION_RESULT_SCHEMA,
        }]

    return handle


def _find_request(context: StageContext):
    for artifact in context.input_artifacts:
        if artifact.get("artifact_type") == ACQUISITION_REQUEST_ARTIFACT_TYPE:
            return artifact
    return None
