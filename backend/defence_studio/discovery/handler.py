"""Durable-worker stage handler for Phase 1A feed discovery.

Binds the discovery service to the locked `news_feed_and_topic_discovery`
stage so provider network access runs only through the verified F2 worker --
never synchronously in an HTTP request. The handler reads the owner-submitted
`discovery_request` artifact (written against the predecessor
`application_foundation` stage), runs discovery, and writes a single
`discovery_result` manifest. A missing or corrupt request fails closed rather
than inventing a default, exactly like F3's acquisition handler.
"""

from __future__ import annotations

import json
import threading

from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.contracts.discovery import (
    DISCOVERY_RESULT_SCHEMA,
    RequestValidationError,
    request_from_dict,
    result_to_dict,
)
from defence_studio.discovery.providers import DiscoveryProviderRegistry
from defence_studio.discovery.service import DiscoveryService
from defence_studio.services.worker import StageContext
from defence_studio.storage.artifacts import ArtifactStore, StoragePathError

DISCOVERY_REQUEST_ARTIFACT_TYPE = "discovery_request"
DISCOVERY_RESULT_ARTIFACT_TYPE = "discovery_result"
DISCOVERY_RESULT_URI_SUFFIX = "research/discovery_result.json"


class DiscoveryHandlerError(Exception):
    """Fail-closed condition that should surface as a stage failure."""


def build_discovery_handler(store: ArtifactStore, registry: DiscoveryProviderRegistry | None = None,
                            guard: UrlGuard | None = None, client_factory=None):
    service = DiscoveryService(guard=guard, registry=registry, client_factory=client_factory)

    def handle(context: StageContext, cancel: threading.Event):
        request_ref = _find_request(context)
        if request_ref is None:
            raise DiscoveryHandlerError(
                "No discovery_request artifact was provided to this stage"
            )
        uri = request_ref["uri"]
        try:
            if store.sha256_of(context.job_id, uri) != request_ref["sha256"]:
                raise DiscoveryHandlerError("Discovery request bytes do not match the reference hash")
            payload = json.loads(store.read_text(context.job_id, uri))
        except StoragePathError as exc:
            raise DiscoveryHandlerError(f"Discovery request path is unsafe: {exc}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise DiscoveryHandlerError(f"Discovery request could not be read: {exc}") from exc

        try:
            request = request_from_dict(payload, job_id=context.job_id)
        except RequestValidationError as exc:
            raise DiscoveryHandlerError(str(exc)) from exc
        if request.job_id != context.job_id:
            raise DiscoveryHandlerError("Discovery request job_id does not match the stage job")

        result = service.run(request, cancel=cancel)

        result_uri = f"jobs/{context.job_id}/research/attempts/{context.claim_id}/discovery_result.json"
        data = json.dumps(result_to_dict(result), indent=2, sort_keys=True).encode("utf-8")
        stored = store.write_bytes(context.job_id, result_uri, data, "application/json")
        return [{
            "artifact_type": DISCOVERY_RESULT_ARTIFACT_TYPE,
            "uri": stored.uri,
            "sha256": stored.sha256,
            "schema_version": DISCOVERY_RESULT_SCHEMA,
        }]

    return handle


def _find_request(context: StageContext):
    for artifact in context.input_artifacts:
        if artifact.get("artifact_type") == DISCOVERY_REQUEST_ARTIFACT_TYPE:
            return artifact
    return None
