"""F4 analysis service and its retry-safe durable-worker handler.

The service reads the real F3 acquisition-result artifact and the byte artifacts
it references, validates each one before trusting it, normalizes text, detects
duplicates, extracts verbatim claims with supporting passages, flags conflicts,
and writes a single versioned, content-addressed ``analysis_result`` artifact.

Durability and idempotency:

* Execution happens through the F2 worker as the stage
  ``extraction_validation_normalization_deduplication``. ``make_analysis_handler``
  returns a handler that is safe to register as retry-safe, because:
  - output is deterministic and written to a content-addressed path keyed by an
    ``input_digest`` over the exact inputs + processor versions, so re-running
    over unchanged inputs reproduces byte-identical output and never overwrites a
    *different* prior result;
  - byte writes are atomic (temp + os.replace) via the F3 store;
  - an interrupted attempt commits nothing (F2 fences it), and the next attempt
    recomputes the same output.
* A missing or corrupt acquisition artifact raises, so the worker records a
  durable stage failure and applies its retry/terminal policy. Per-source
  problems are recorded as failures/warnings and processing continues.

The service verifies job ownership on every call, so a caller acting for the
wrong owner is rejected exactly as F1 rejects it (404-equivalent).
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.analysis import claims as claim_extraction
from defence_studio.analysis import dedup as dedup_mod
from defence_studio.analysis import normalize as normalize_mod
from defence_studio.analysis import relationships
from defence_studio.contracts.analysis import (
    ACQUISITION_RESULT_ARTIFACT,
    ANALYSIS_RESULT_ARTIFACT,
    ANALYSIS_RESULT_SCHEMA,
    ANALYSIS_STAGE,
    DEDUP_VERSION,
    EXTRACTOR_VERSION,
    NORMALIZER_VERSION,
    AnalysisLimits,
    AnalysisResultV1,
    AnalysisVersions,
    NormalizationStatus,
    NormalizedSourceV1,
    OriginalArtifactRef,
    ProcessingFailure,
    ProcessingWarning,
    SourceDates,
    result_to_dict,
)
from defence_studio.services.jobs import JobService
from defence_studio.storage.artifacts import ArtifactStore, StoragePathError

# Re-exported so a host can register the handler against the correct locked stage
# without reaching into the contracts module directly.
__all__ = ["AnalysisService", "AnalysisInputError", "make_analysis_handler", "ANALYSIS_STAGE"]


class AnalysisInputError(Exception):
    """The predecessor acquisition artifact is missing or corrupt — the stage
    cannot proceed. Propagated so the durable worker records a stage failure."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hard_limits(requested: AnalysisLimits) -> AnalysisLimits:
    """Clamp a request against hard ceilings: it may ask for the same or less."""
    ceiling = AnalysisLimits()
    return AnalysisLimits(
        max_sources=min(requested.max_sources, ceiling.max_sources),
        max_bytes_per_source=min(requested.max_bytes_per_source, ceiling.max_bytes_per_source),
        max_text_chars=min(requested.max_text_chars, ceiling.max_text_chars),
        max_claims_per_source=min(requested.max_claims_per_source, ceiling.max_claims_per_source),
        near_duplicate_threshold=min(max(requested.near_duplicate_threshold, 0.5), 1.0),
        min_claim_chars=max(requested.min_claim_chars, 1),
        max_claim_chars=min(requested.max_claim_chars, ceiling.max_claim_chars),
    )


class AnalysisService:
    def __init__(self, database_file: Path, storage_root: Path):
        self.jobs = JobService(database_file)
        self.store = ArtifactStore(storage_root)

    # -- public API ---------------------------------------------------------

    def analyze(
        self,
        job_id: str,
        owner_id: str,
        acquisition_uri: str,
        acquisition_sha256: str,
        topic: str = "",
        limits: AnalysisLimits | None = None,
        should_cancel=None,
    ) -> AnalysisResultV1:
        # Ownership: raises JobNotFound (404) for a wrong/absent owner.
        self.jobs.get(job_id, owner_id)
        limits = _hard_limits(limits or AnalysisLimits())
        acquisition = self._load_acquisition(job_id, acquisition_uri, acquisition_sha256)

        normalized: list[NormalizedSourceV1] = []
        failures: list[ProcessingFailure] = []
        warnings: list[ProcessingWarning] = []
        cue_index: dict = {}

        sources = acquisition.get("sources") or []
        if len(sources) > limits.max_sources:
            warnings.append(ProcessingWarning(
                code="SOURCES_TRUNCATED",
                message=f"Acquisition listed {len(sources)} sources; processing the first "
                        f"{limits.max_sources} per request limit.",
            ))
            sources = sources[: limits.max_sources]

        for source in sources:
            if should_cancel is not None and should_cancel():
                raise AnalysisInputError("Processing cancelled: worker lease lost")
            record, failure, source_warnings, cues = self._normalize_source(job_id, source, limits)
            if record is not None:
                normalized.append(record)
                if cues:
                    cue_index[record.source_id] = cues
            if failure is not None:
                failures.append(failure)
            warnings.extend(source_warnings)

        dedup_inputs = [
            dedup_mod.DedupInput(
                source_id=rec.source_id, canonical_url=rec.canonical_url,
                normalized_sha256=rec.normalized_sha256, normalized_text=rec.normalized_text,
            )
            for rec in normalized if rec.status == NormalizationStatus.NORMALIZED
        ]
        duplicate_groups = dedup_mod.find_duplicate_groups(
            dedup_inputs, limits.near_duplicate_threshold
        )

        extracted = []
        for rec in normalized:
            if rec.status != NormalizationStatus.NORMALIZED:
                continue
            extracted.extend(claim_extraction.extract_claims(
                source_id=rec.source_id,
                text=rec.normalized_text,
                language=rec.language_detected,
                cues=cue_index.get(rec.source_id, ()),
                artifact_uri=rec.original_artifact.uri if rec.original_artifact else None,
                source_published_at=rec.dates.published_at,
                acquisition_timestamp=rec.dates.acquired_at,
                topic=topic,
                limits=limits,
            ))

        claim_groups = relationships.group_claims(extracted, limits.near_duplicate_threshold)
        conflicts = relationships.find_conflicts(extracted)

        normalized_ok = sum(1 for r in normalized if r.status == NormalizationStatus.NORMALIZED)
        status = "COMPLETED" if not failures else "COMPLETED_WITH_FAILURES"
        input_digest = self._input_digest(acquisition_sha256, normalized, limits)

        return AnalysisResultV1(
            job_id=job_id,
            analysis_timestamp=_utc(),
            input_acquisition_uri=acquisition_uri,
            input_acquisition_sha256=acquisition_sha256,
            input_digest=input_digest,
            status=status,
            normalized_sources=tuple(normalized),
            duplicate_groups=tuple(duplicate_groups),
            claims=tuple(extracted),
            claim_groups=tuple(claim_groups),
            conflicts=tuple(conflicts),
            warnings=tuple(warnings),
            failures=tuple(failures),
            sources_total=len(sources),
            sources_normalized=normalized_ok,
            sources_failed=len(sources) - normalized_ok,
            claims_total=len(extracted),
            versions=AnalysisVersions(),
        )

    def run_and_store(
        self,
        job_id: str,
        owner_id: str,
        acquisition_uri: str,
        acquisition_sha256: str,
        topic: str = "",
        limits: AnalysisLimits | None = None,
        should_cancel=None,
    ) -> tuple[dict, AnalysisResultV1]:
        """Run analysis and persist a versioned, content-addressed artifact.

        Returns the artifact reference (for the worker to commit) and the result.
        If an artifact already exists at the input-digest path (a prior run over
        the same inputs/versions), it is reused as-is rather than rewritten: the
        analysis still runs so the caller gets a result, but the stored bytes —
        and therefore the artifact's sha256 — are not disturbed by this run's
        wall-clock ``analysis_timestamp``. This is what makes repeat processing
        over unchanged inputs genuinely idempotent rather than merely same-path."""
        result = self.analyze(job_id, owner_id, acquisition_uri, acquisition_sha256,
                              topic, limits, should_cancel)
        relative_uri = f"jobs/{job_id}/research/analysis/{result.input_digest}.json"
        if self.store.exists(job_id, relative_uri):
            existing_sha = self.store.sha256_of(job_id, relative_uri)
            ref = {
                "artifact_type": ANALYSIS_RESULT_ARTIFACT,
                "uri": relative_uri,
                "sha256": existing_sha,
                "schema_version": ANALYSIS_RESULT_SCHEMA,
            }
            return ref, result
        payload = json.dumps(result_to_dict(result), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        stored = self.store.write_bytes(job_id, relative_uri, payload, "application/json")
        ref = {
            "artifact_type": ANALYSIS_RESULT_ARTIFACT,
            "uri": stored.uri,
            "sha256": stored.sha256,
            "schema_version": ANALYSIS_RESULT_SCHEMA,
        }
        return ref, result

    # -- internals ----------------------------------------------------------

    def _load_acquisition(self, job_id: str, uri: str, expected_sha: str) -> dict:
        try:
            exists = self.store.exists(job_id, uri)
        except StoragePathError as exc:
            raise AnalysisInputError(f"Acquisition artifact URI is unsafe: {exc}") from None
        if not exists:
            raise AnalysisInputError("Acquisition result artifact is missing")
        actual_sha = self.store.sha256_of(job_id, uri)
        if actual_sha != expected_sha:
            raise AnalysisInputError(
                "Acquisition result artifact failed integrity check "
                f"(expected {expected_sha}, found {actual_sha})"
            )
        try:
            parsed = json.loads(self.store.read_text(job_id, uri))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AnalysisInputError(f"Acquisition result artifact is not valid JSON: {exc}") from None
        if not isinstance(parsed, dict):
            raise AnalysisInputError("Acquisition result artifact is not a JSON object")
        return parsed

    def _normalize_source(self, job_id: str, source: dict, limits: AnalysisLimits):
        source_id = str(source.get("source_id") or "")
        warnings: list[ProcessingWarning] = []
        declared = source.get("declared_metadata") or {}
        dates = SourceDates(
            published_at=declared.get("published_at")
            if declared.get("published_at_status") == "COLLECTED" else None,
            acquired_at=source.get("acquisition_timestamp"),
        )
        base = dict(
            source_id=source_id,
            target_id=str(source.get("target_id") or ""),
            source_type=source.get("source_type"),
            requested_url=str(source.get("requested_url") or ""),
            canonical_url=str(source.get("canonical_url") or ""),
            final_url=source.get("final_url"),
            declared_metadata=declared,
            dates=dates,
        )

        if source.get("status") != "ACQUIRED":
            warnings.append(ProcessingWarning(
                code="SOURCE_NOT_ACQUIRED",
                message=f"F3 marked this source {source.get('status')!r}; no content to normalize.",
                source_id=source_id,
            ))
            return self._failed_source(base, media_type=None,
                                       reason="Source was not successfully acquired"), None, warnings, ()

        artifact = self._pick_artifact(source.get("artifacts") or [])
        if artifact is None:
            failure = ProcessingFailure(
                code="NO_SUPPORTED_ARTIFACT", retryable=False, source_id=source_id,
                message="No artifact with a supported media type was available to normalize.",
            )
            return self._failed_source(base, media_type=None,
                                       reason=failure.message), failure, warnings, ()

        media_type = str(artifact.get("media_type") or "")
        uri = str(artifact.get("uri") or "")
        recorded_sha = str(artifact.get("sha256") or "")
        recorded_bytes = int(artifact.get("bytes") or 0)

        if recorded_bytes > limits.max_bytes_per_source:
            failure = ProcessingFailure(
                code="ARTIFACT_TOO_LARGE", retryable=False, source_id=source_id,
                message=f"Artifact {recorded_bytes} bytes exceeds limit {limits.max_bytes_per_source}.",
            )
            return self._failed_source(base, media_type=media_type,
                                       reason=failure.message), failure, warnings, ()

        try:
            data = self.store.read_bytes(job_id, uri, max_bytes=limits.max_bytes_per_source)
        except (StoragePathError, FileNotFoundError, OSError) as exc:
            failure = ProcessingFailure(
                code="ARTIFACT_UNREADABLE", retryable=False, source_id=source_id,
                message=f"Could not read artifact {uri!r}: {exc}",
            )
            return self._failed_source(base, media_type=media_type,
                                       reason=failure.message), failure, warnings, ()

        actual_sha = hashlib.sha256(data).hexdigest()
        sha_ok = bool(recorded_sha) and actual_sha == recorded_sha
        if recorded_sha and not sha_ok:
            failure = ProcessingFailure(
                code="ARTIFACT_CORRUPT", retryable=False, source_id=source_id,
                message=f"Artifact bytes do not match recorded SHA-256 for {uri!r}.",
            )
            return self._failed_source(base, media_type=media_type,
                                       reason=failure.message), failure, warnings, ()

        try:
            normalized = normalize_mod.normalize_bytes(data, media_type)
        except normalize_mod.NormalizationError as exc:
            failure = ProcessingFailure(
                code="NORMALIZATION_FAILED", retryable=False, source_id=source_id,
                message=f"Could not normalize {media_type!r} content: {exc}",
            )
            return self._failed_source(base, media_type=media_type,
                                       reason=failure.message), failure, warnings, ()

        text = normalized.text
        if len(text) > limits.max_text_chars:
            warnings.append(ProcessingWarning(
                code="TEXT_TRUNCATED", source_id=source_id,
                message=f"Normalized text capped at {limits.max_text_chars} characters for extraction.",
            ))
            text = text[: limits.max_text_chars]
        cues = tuple(c for c in normalized.cues if c.text_start < len(text))

        record = NormalizedSourceV1(
            status=NormalizationStatus.NORMALIZED,
            media_type=media_type,
            language_detected=normalized.language,
            normalized_text=text,
            normalized_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            normalized_char_count=len(text),
            original_artifact=OriginalArtifactRef(
                artifact_type=str(artifact.get("artifact_type") or ""),
                uri=uri, sha256=actual_sha, bytes=len(data), media_type=media_type,
            ),
            original_sha256_verified=sha_ok,
            warnings=tuple(w.message for w in warnings if w.source_id == source_id),
            **base,
        )
        return record, None, warnings, cues

    @staticmethod
    def _failed_source(base: dict, media_type, reason: str) -> NormalizedSourceV1:
        return NormalizedSourceV1(
            status=NormalizationStatus.FAILED,
            media_type=media_type,
            language_detected="und",
            normalized_text="",
            normalized_sha256="",
            normalized_char_count=0,
            original_artifact=None,
            original_sha256_verified=False,
            warnings=(reason,),
            **base,
        )

    @staticmethod
    def _pick_artifact(artifacts: list) -> dict | None:
        """Pick the first artifact whose media type we can normalize. Subtitles
        win over bulky raw pages when both are present (spoken text is enough)."""
        supported = (normalize_mod._SUBTITLE_TYPES | normalize_mod._HTML_TYPES
                     | normalize_mod._PLAIN_TYPES)
        ranked = sorted(
            [a for a in artifacts if isinstance(a, dict)],
            key=lambda a: 0 if (str(a.get("media_type") or "").split(";", 1)[0].strip().lower()
                                in normalize_mod._SUBTITLE_TYPES) else 1,
        )
        for artifact in ranked:
            media = str(artifact.get("media_type") or "").split(";", 1)[0].strip().lower()
            if media in supported:
                return artifact
        return None

    @staticmethod
    def _input_digest(acquisition_sha: str, normalized: list[NormalizedSourceV1],
                      limits: AnalysisLimits) -> str:
        identity = {
            "acquisition_sha256": acquisition_sha,
            "sources": sorted(
                [rec.source_id,
                 rec.original_artifact.sha256 if rec.original_artifact else "",
                 rec.status.value]
                for rec in normalized
            ),
            "versions": [NORMALIZER_VERSION, EXTRACTOR_VERSION, DEDUP_VERSION],
            "limits": asdict(limits),
        }
        blob = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()


def make_analysis_handler(database_file: Path, storage_root: Path):
    """Build a retry-safe worker handler for the analysis stage.

    Register it as ``{ANALYSIS_STAGE: handler}`` with ``retry_safe_stages``
    containing the same stage. Repeated execution is safe: output is
    deterministic and content-addressed (see module docstring)."""
    service = AnalysisService(database_file, storage_root)

    def handler(context, cancel: threading.Event):
        acquisition_ref = next(
            (a for a in context.input_artifacts
             if a.get("artifact_type") == ACQUISITION_RESULT_ARTIFACT),
            None,
        )
        if acquisition_ref is None:
            raise AnalysisInputError(
                "No acquisition_result artifact from the predecessor stage to analyze"
            )
        ref, _result = service.run_and_store(
            job_id=context.job_id,
            owner_id=context.owner_id,
            acquisition_uri=acquisition_ref["uri"],
            acquisition_sha256=acquisition_ref["sha256"],
            topic=context.topic,
            should_cancel=cancel.is_set,
        )
        return [ref]

    return handler
