"""Durable-worker stage handler for F3 content acquisition.

Binds the acquisition service to the locked `topic_search_and_source_collection`
stage so acquisition runs only through the verified F2 worker — never
synchronously in an HTTP request and never through a side queue.

The handler is retry-safe. It prefers an explicit ``acquisition_request``
from an earlier stage. When the owner did not confirm a subset, it builds
targets from the ``discovery_result`` candidates and records that derived
request beside the manifest. A discovery result with no fetchable URL
completes with zero sources and a warning; it does not pretend pages were
fetched. A missing discovery result and a missing request fail closed.
Already-stored, hash-verified sources are reused instead of re-downloaded.
"""

from __future__ import annotations

import json
import threading

from defence_studio.acquisition.service import AcquisitionService
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.contracts.acquisition import (
    ACQUISITION_REQUEST_SCHEMA,
    ACQUISITION_RESULT_SCHEMA,
    AcquisitionLimits,
    AcquisitionResultV1,
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
        derived = None
        note = ""
        request_ref = _find_request(context)
        if request_ref is None:
            targets, note = _targets_from_discovery(store, context)
            if not targets:
                result = _empty_result(context.job_id, note)
                return [_store_manifest(store, context.job_id, result, context.claim_id)]
            payload = {
                "schema_version": ACQUISITION_REQUEST_SCHEMA,
                "job_id": context.job_id,
                "targets": targets,
                "selection": "automatic_from_discovery",
            }
            derived = _store_json(
                store, context.job_id,
                f"jobs/{context.job_id}/research/attempts/{context.claim_id}/automatic_acquisition_request.json",
                payload, ACQUISITION_REQUEST_ARTIFACT_TYPE, ACQUISITION_REQUEST_SCHEMA,
            )
        else:
            payload = _read_ref(store, context, request_ref, "Acquisition request")

        try:
            request = request_from_dict(payload)
        except RequestValidationError as exc:
            raise AcquisitionHandlerError(str(exc)) from exc
        if request.job_id != context.job_id:
            raise AcquisitionHandlerError("Acquisition request job_id does not match the stage job")

        result = service.run(request, cancel=cancel)
        if derived is not None and note:
            from dataclasses import replace
            result = replace(result, warnings=tuple(result.warnings) + (note,))
        outputs = []
        if derived is not None:
            outputs.append(derived)
        outputs.append(_store_manifest(store, context.job_id, result, context.claim_id))
        return outputs

    return handle


def _find_request(context: StageContext):
    for artifact in context.input_artifacts:
        if artifact.get("artifact_type") == ACQUISITION_REQUEST_ARTIFACT_TYPE:
            return artifact
    return None


def _find_discovery(context: StageContext):
    for artifact in context.input_artifacts:
        if artifact.get("artifact_type") == "discovery_result":
            return artifact
    return None


def _read_ref(store: ArtifactStore, context: StageContext, ref: dict, label: str) -> dict:
    uri = ref["uri"]
    try:
        if store.sha256_of(context.job_id, uri) != ref["sha256"]:
            raise AcquisitionHandlerError(f"{label} bytes do not match the reference hash")
        return json.loads(store.read_text(context.job_id, uri))
    except StoragePathError as exc:
        raise AcquisitionHandlerError(f"{label} path is unsafe: {exc}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AcquisitionHandlerError(f"{label} could not be read: {exc}") from exc


def _targets_from_discovery(store: ArtifactStore, context: StageContext):
    """Ranked discovery candidates become acquisition targets.

    An explicit acquisition_request is never invented here; callers use this
    only when the owner did not confirm a subset. Hosted-search misses stay
    in the discovery provider outcomes and are not turned into URLs.
    """
    ref = _find_discovery(context)
    if ref is None:
        raise AcquisitionHandlerError(
            "No acquisition_request or discovery_result was provided to this stage"
        )
    payload = _read_ref(store, context, ref, "Discovery result")
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list):
        raise AcquisitionHandlerError("Discovery result candidates must be a list")
    limit = AcquisitionLimits().max_sources
    targets = []
    skipped = 0
    for candidate in candidates:
        if len(targets) >= limit:
            break
        if not isinstance(candidate, dict):
            skipped += 1
            continue
        url = candidate.get("canonical_url") or ""
        cid = candidate.get("candidate_id")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            skipped += 1
            continue
        if not isinstance(cid, str) or not cid.strip():
            skipped += 1
            continue
        declared = candidate.get("declared_metadata") if isinstance(candidate.get("declared_metadata"), dict) else {}
        language = declared.get("language")
        title = declared.get("title")
        targets.append({
            "target_id": cid,
            "url": url,
            "method": "auto",
            "language_preferences": [language] if isinstance(language, str) and language else [],
            "title_hint": title if isinstance(title, str) else None,
        })
    if not targets:
        note = "Discovery returned no fetchable article URLs; nothing was fetched."
        if skipped:
            note = f"Discovery listed {skipped} candidate(s) without a usable http(s) URL; nothing was fetched."
        return [], note
    note = ""
    if len(candidates) > len(targets):
        note = f"Acquiring the first {len(targets)} of {len(candidates)} discovered URLs."
    return targets, note


def _empty_result(job_id: str, note: str) -> AcquisitionResultV1:
    from datetime import datetime, timezone
    return AcquisitionResultV1(
        job_id=job_id,
        acquired_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        sources=(),
        requested=0,
        succeeded=0,
        failed=0,
        skipped_duplicate=0,
        warnings=(note,),
    )


def _store_manifest(store: ArtifactStore, job_id: str, result: AcquisitionResultV1, claim_id: str) -> dict:
    return _store_json(
        store, job_id,
        f"jobs/{job_id}/research/attempts/{claim_id}/acquisition_result.json",
        result_to_dict(result),
        ACQUISITION_MANIFEST_ARTIFACT_TYPE,
        ACQUISITION_RESULT_SCHEMA,
    )


def _store_json(store, job_id, uri, payload, artifact_type, schema_version) -> dict:
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    stored = store.write_bytes(job_id, uri, data, "application/json")
    return {
        "artifact_type": artifact_type,
        "uri": stored.uri,
        "sha256": stored.sha256,
        "schema_version": schema_version,
    }
