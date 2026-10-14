"""Core alignment orchestration (engine-agnostic, side-effect free).

``run_alignment`` performs, in order:
1. Transcript-hash validation: recompute the transcript digest and compare to
   the request's ``transcript_sha256``.
2. Path safety + audio presence + size bound + audio-hash validation.
3. Real audio inspection for measured duration (never estimated).
4. Synthetic-engine guard (refuse the test aligner unless opted in).
5. Tokenization preserving the exact original text and char spans.
6. Engine alignment of spoken tokens.
7. Structural validation of engine timing (finite, non-negative, ordered,
   within measured duration; unique token IDs) — malformed timing raises
   ``AlignmentOutputInvalidError`` rather than yielding a trusted artifact.
8. Deterministic output assembly with an artifact hash.

It writes nothing and runs no job bookkeeping — that is the job store's role —
so it is a pure function of (request, engine, audio bytes).
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timezone

from align_engine._hashing import sha256_file
from align_engine.audio import probe_audio, resolve_input_path
from align_engine.config import AlignConfig
from align_engine.contracts import (
    OUTPUT_SCHEMA, AlignmentRequestV1, build_output, transcript_digest,
)
from align_engine.engines.base import AlignmentEngine, EngineTokenInput
from align_engine.errors import (
    AlignmentOutputInvalidError, HashMismatchError, UnsupportedCapabilityError,
    UnsupportedLanguageError,
)
from align_engine.normalize import tokenize_segment

_TIME_EPSILON = 1e-6
VALIDATOR_VERSION = "defence-align.output-validator.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def run_alignment(request: AlignmentRequestV1, engine: AlignmentEngine, config: AlignConfig,
                  *, cancel: threading.Event | None = None) -> dict:
    # 1. Transcript integrity.
    if transcript_digest(request) != request.transcript_sha256:
        raise HashMismatchError("transcript_sha256 does not match the supplied transcript content")

    # 2-3. Audio path safety, presence, size, hash, measured duration.
    audio_path = resolve_input_path(config.input_root, request.audio_path)
    audio_info = probe_audio(audio_path, max_bytes=config.max_audio_bytes)
    actual_audio_sha = sha256_file(audio_path)
    if actual_audio_sha != request.audio_sha256:
        raise HashMismatchError("audio_sha256 does not match the audio file bytes")

    caps = engine.capabilities()
    # 4. Synthetic guard — never let synthetic timings masquerade as measured.
    if caps.is_synthetic and not request.config.allow_synthetic:
        raise UnsupportedCapabilityError(
            "Engine is synthetic; set config.allow_synthetic=true to accept synthetic "
            "(test-only) alignment, which must never be treated as measured alignment."
        )
    if not caps.available:
        raise UnsupportedCapabilityError(f"Engine {caps.engine_name!r} is not available: {caps.notes}")
    if request.language not in caps.declared_languages:
        raise UnsupportedLanguageError(
            f"Engine {caps.engine_name!r} does not declare support for language {request.language!r}"
        )

    # 5. Tokenize preserving original text + spans.
    section_tokens: dict[str, dict[str, list]] = {}
    spoken_inputs: list[EngineTokenInput] = []
    index = 0
    for section in request.sections:
        section_tokens[section.section_id] = {}
        for segment in section.segments:
            toks = tokenize_segment(segment.segment_id, segment.original_text)
            section_tokens[section.section_id][segment.segment_id] = toks
            for tok in toks:
                if tok.kind == "word":
                    spoken_inputs.append(EngineTokenInput(
                        token_id=tok.token_id, original_text=tok.original_text,
                        normalized_text=tok.normalized_text, segment_id=segment.segment_id,
                        index=index,
                    ))
                    index += 1

    # 6. Engine alignment.
    result = engine.align(
        audio_path=audio_path, duration_seconds=audio_info.duration_seconds,
        language=request.language, tokens=spoken_inputs,
        requested_granularity=request.config.granularity, cancel=cancel,
    )
    by_id = {r.token_id: r for r in result.tokens}

    # 7. Structural validation of engine timing.
    issues: list[dict] = []
    seen_ids: set[str] = set()
    last_start = -math.inf
    duration = audio_info.duration_seconds
    for r in result.tokens:
        if r.token_id in seen_ids:
            issues.append(dict(code="DUPLICATE_TOKEN", severity="ERROR",
                               detail=f"Engine returned duplicate token id {r.token_id!r}", token_id=r.token_id))
        seen_ids.add(r.token_id)
        if not r.aligned:
            continue
        s, e = r.start, r.end
        if s is None or e is None or not (math.isfinite(s) and math.isfinite(e)):
            issues.append(dict(code="NON_FINITE_TIMESTAMP", severity="ERROR",
                               detail=f"Token {r.token_id!r} has non-finite timing", token_id=r.token_id))
            continue
        if s < -_TIME_EPSILON or e < -_TIME_EPSILON:
            issues.append(dict(code="NEGATIVE_TIMESTAMP", severity="ERROR",
                               detail=f"Token {r.token_id!r} has a negative timestamp", token_id=r.token_id))
        if e + _TIME_EPSILON < s:
            issues.append(dict(code="END_BEFORE_START", severity="ERROR",
                               detail=f"Token {r.token_id!r} ends before it starts", token_id=r.token_id))
        if s > duration + _TIME_EPSILON or e > duration + _TIME_EPSILON:
            issues.append(dict(code="OUT_OF_DURATION", severity="ERROR",
                               detail=f"Token {r.token_id!r} timing {s}-{e} exceeds measured "
                                      f"audio duration {duration}", token_id=r.token_id))
        if s + _TIME_EPSILON < last_start:
            issues.append(dict(code="OUT_OF_ORDER", severity="ERROR",
                               detail=f"Token {r.token_id!r} starts before the previous token", token_id=r.token_id))
        last_start = max(last_start, s)

    if request.config.granularity != result.actual_granularity:
        issues.append(dict(
            code="GRANULARITY_DOWNGRADED", severity="WARNING",
            detail=f"Requested {request.config.granularity!r} but the engine produced "
                   f"{result.actual_granularity!r}; output is labelled with the actual granularity.",
        ))

    if any(i["severity"] == "ERROR" for i in issues):
        raise AlignmentOutputInvalidError(
            "Engine returned structurally invalid timing; refusing to emit a trusted artifact.",
            issues=issues,
        )

    # 8. Assemble the deterministic output.
    out_sections, unaligned = _assemble_sections(
        request, section_tokens, by_id, result.actual_granularity,
    )
    for u in unaligned:
        issues.append(dict(code="TOKEN_UNALIGNED", severity="INFO",
                           detail=f"Token {u['token_id']!r} not aligned: {u['reason']}", token_id=u["token_id"]))

    body = {
        "schema_version": OUTPUT_SCHEMA,
        "alignment_job_id": request.alignment_job_id,
        "project_id": request.project_id,
        "audio_artifact_id": request.audio_artifact_id,
        "audio_sha256": request.audio_sha256,
        "transcript_id": request.transcript_id,
        "transcript_sha256": request.transcript_sha256,
        "language": request.language,
        "audio_duration_seconds": duration,
        "engine_name": caps.engine_name,
        "engine_version": caps.engine_version,
        "is_synthetic": caps.is_synthetic,
        "requested_granularity": request.config.granularity,
        "actual_granularity": result.actual_granularity,
        "sections": out_sections,
        "unaligned_tokens": unaligned,
        "validation": {"passed": True, "validator_version": VALIDATOR_VERSION, "issues": issues},
        "created_at": _now(),
    }
    return build_output(body)


def _assemble_sections(request, section_tokens, by_id, actual_granularity):
    out_sections = []
    unaligned = []
    for section in request.sections:
        out_segments = []
        for segment in section.segments:
            toks = section_tokens[section.section_id][segment.segment_id]
            records = []
            if actual_granularity == "sentence":
                # One timed "segment" token plus the original word/punctuation
                # tokens carried untimed for reconstruction.
                seg_result = by_id.get(segment.segment_id)
                records.append(_segment_record(segment, seg_result))
                for tok in toks:
                    records.append(_untimed_record(tok))
            else:
                for tok in toks:
                    if tok.kind != "word":
                        records.append(_untimed_record(tok))
                        continue
                    r = by_id.get(tok.token_id)
                    if r is None or not r.aligned:
                        reason = (r.reason if r and r.reason else "engine returned no alignment")
                        unaligned.append({"token_id": tok.token_id, "segment_id": segment.segment_id,
                                          "original_text": tok.original_text, "reason": reason})
                        records.append(_untimed_record(tok, uncertain=True))
                    else:
                        records.append({
                            "token_id": tok.token_id, "kind": "word",
                            "original_text": tok.original_text, "normalized_text": tok.normalized_text,
                            "char_start": tok.char_start, "char_end": tok.char_end,
                            "start": r.start, "end": r.end, "confidence": r.confidence,
                            "is_uncertain": r.is_uncertain, "aligned": True,
                        })
            out_segments.append({"segment_id": segment.segment_id,
                                 "original_text": segment.original_text, "tokens": records})
        out_sections.append({"section_id": section.section_id, "segments": out_segments})
    return out_sections, unaligned


def _untimed_record(tok, uncertain: bool = False) -> dict:
    return {
        "token_id": tok.token_id, "kind": tok.kind, "original_text": tok.original_text,
        "normalized_text": tok.normalized_text, "char_start": tok.char_start,
        "char_end": tok.char_end, "start": None, "end": None, "confidence": None,
        "is_uncertain": uncertain, "aligned": False,
    }


def _segment_record(segment, seg_result) -> dict:
    aligned = seg_result is not None and seg_result.aligned
    return {
        "token_id": segment.segment_id, "kind": "segment",
        "original_text": segment.original_text, "normalized_text": segment.original_text,
        "char_start": 0, "char_end": len(segment.original_text),
        "start": seg_result.start if aligned else None,
        "end": seg_result.end if aligned else None,
        "confidence": seg_result.confidence if aligned else None,
        "is_uncertain": False, "aligned": aligned,
    }
