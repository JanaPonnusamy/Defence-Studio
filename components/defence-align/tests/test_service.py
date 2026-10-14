"""End-to-end service: validation, timestamp checks, deterministic output."""
import threading

import pytest

from align_engine.contracts import AlignmentOutputV1
from align_engine.engines import DeterministicTestAligner
from align_engine.engines.base import (
    AlignmentEngine, EngineAlignResult, EngineCapabilities, EngineTokenResult,
)
from align_engine.errors import (
    AlignmentOutputInvalidError, HashMismatchError, UnsupportedCapabilityError,
    UnsupportedLanguageError,
)
from align_engine.service import run_alignment
from conftest import TA_SECTIONS, build_request, make_config


def _caps(available=True, synthetic=False, langs=("en", "ta")):
    return EngineCapabilities(
        engine_name="double", engine_version="1", available=available, is_synthetic=synthetic,
        declared_languages=langs, tested_languages=langs, audio_formats=("wav",),
        granularities=("word", "sentence"), requires_model=False, notes="")


class _ConstEngine(AlignmentEngine):
    """Returns caller-supplied token results verbatim, for validation tests."""
    def __init__(self, results, granularity="word", caps=None):
        self._results, self._granularity, self._caps = results, granularity, caps or _caps()

    def capabilities(self):
        return self._caps

    def align(self, **kw):
        return EngineAlignResult(actual_granularity=self._granularity, tokens=self._results)


def test_happy_path_synthetic_output_is_valid(config):
    req = build_request(config)
    out = run_alignment(req, DeterministicTestAligner(), config)
    AlignmentOutputV1.model_validate(out)
    assert out["is_synthetic"] is True
    assert out["engine_name"] == "deterministic-test-aligner"
    assert out["actual_granularity"] == "word"
    assert out["audio_duration_seconds"] == pytest.approx(1.0)
    words = [t for s in out["sections"] for seg in s["segments"] for t in seg["tokens"] if t["kind"] == "word"]
    assert words and all(0 <= t["start"] <= t["end"] <= 1.0 for t in words)
    # Punctuation present but never timed / never a spoken word.
    puncts = [t for s in out["sections"] for seg in s["segments"] for t in seg["tokens"] if t["kind"] == "punctuation"]
    assert puncts and all(t["start"] is None and not t["aligned"] for t in puncts)


def test_synthetic_engine_refused_without_opt_in(config):
    req = build_request(config, allow_synthetic=False)
    with pytest.raises(UnsupportedCapabilityError, match="synthetic"):
        run_alignment(req, DeterministicTestAligner(), config)


def test_audio_hash_mismatch_rejected(config):
    req = build_request(config)
    tampered = req.model_copy(update={"audio_sha256": "a" * 64})
    with pytest.raises(HashMismatchError, match="audio"):
        run_alignment(tampered, DeterministicTestAligner(), config)


def test_transcript_hash_mismatch_rejected(config):
    req = build_request(config)
    tampered = req.model_copy(update={"transcript_sha256": "b" * 64})
    with pytest.raises(HashMismatchError, match="transcript"):
        run_alignment(tampered, DeterministicTestAligner(), config)


def test_out_of_duration_timestamp_is_rejected(config):
    req = build_request(config, allow_synthetic=False)
    # token id must match a real spoken token: first word of first segment.
    bad = [EngineTokenResult(token_id="s1-seg1:t0", aligned=True, start=0.0, end=99.0)]
    with pytest.raises(AlignmentOutputInvalidError) as exc:
        run_alignment(req, _ConstEngine(bad), config)
    assert any(i["code"] == "OUT_OF_DURATION" for i in exc.value.issues)


def test_out_of_order_timestamps_rejected(config):
    req = build_request(config, allow_synthetic=False)
    bad = [EngineTokenResult(token_id="s1-seg1:t0", aligned=True, start=0.9, end=1.0),
           EngineTokenResult(token_id="s1-seg1:t1", aligned=True, start=0.1, end=0.2)]
    with pytest.raises(AlignmentOutputInvalidError) as exc:
        run_alignment(req, _ConstEngine(bad), config)
    assert any(i["code"] == "OUT_OF_ORDER" for i in exc.value.issues)


def test_duplicate_token_ids_rejected(config):
    req = build_request(config, allow_synthetic=False)
    dup = [EngineTokenResult(token_id="s1-seg1:t0", aligned=True, start=0.0, end=0.1),
           EngineTokenResult(token_id="s1-seg1:t0", aligned=True, start=0.1, end=0.2)]
    with pytest.raises(AlignmentOutputInvalidError) as exc:
        run_alignment(req, _ConstEngine(dup), config)
    assert any(i["code"] == "DUPLICATE_TOKEN" for i in exc.value.issues)


def test_unaligned_tokens_are_recorded_not_failed(config):
    req = build_request(config, allow_synthetic=False)
    # Align only the first spoken token; leave the rest unaligned explicitly.
    results = [EngineTokenResult(token_id="s1-seg1:t0", aligned=True, start=0.0, end=0.2)]
    out = run_alignment(req, _ConstEngine(results), config)
    assert out["validation"]["passed"] is True
    assert out["unaligned_tokens"], "unaligned tokens must be reported explicitly"
    reasons = {u["reason"] for u in out["unaligned_tokens"]}
    assert reasons  # machine-readable reason present


def test_sentence_only_engine_is_not_labelled_word_level(config):
    req = build_request(config, granularity="word", allow_synthetic=False)
    seg_results = [EngineTokenResult(token_id="s1-seg1", aligned=True, start=0.0, end=0.5, kind="segment"),
                   EngineTokenResult(token_id="s1-seg2", aligned=True, start=0.5, end=1.0, kind="segment")]
    engine = _ConstEngine(seg_results, granularity="sentence")
    out = run_alignment(req, engine, config)
    assert out["requested_granularity"] == "word"
    assert out["actual_granularity"] == "sentence"   # truthfully labelled
    assert any(i["code"] == "GRANULARITY_DOWNGRADED" for i in out["validation"]["issues"])


def test_unsupported_language_for_engine(config):
    req = build_request(config, language="ta", allow_synthetic=False)
    engine = _ConstEngine([], caps=_caps(langs=("en",)))
    with pytest.raises(UnsupportedLanguageError):
        run_alignment(req, engine, config)


def test_unavailable_engine_rejected(config):
    req = build_request(config, allow_synthetic=False)
    engine = _ConstEngine([], caps=_caps(available=False))
    with pytest.raises(UnsupportedCapabilityError):
        run_alignment(req, engine, config)


def test_tamil_pipeline_runs_and_preserves_unicode(config):
    req = build_request(config, sections=TA_SECTIONS, language="ta")
    out = run_alignment(req, DeterministicTestAligner(), config)
    joined = "".join(t["original_text"] for s in out["sections"] for seg in s["segments"] for t in seg["tokens"])
    assert "ராஃபேல்" in joined and "DRDO" in joined and "AESA" in joined


def test_deterministic_serialization_excludes_created_at(config):
    req = build_request(config)
    out1 = run_alignment(req, DeterministicTestAligner(), config)
    out2 = run_alignment(req, DeterministicTestAligner(), config)
    assert out1["artifact_sha256"] == out2["artifact_sha256"]
    # created_at is allowed to differ and is excluded from the hash.
    assert out1["created_at"] != out2["created_at"] or out1 == out2
    cancel = threading.Event()
    out3 = run_alignment(req, DeterministicTestAligner(), config, cancel=cancel)
    assert out3["artifact_sha256"] == out1["artifact_sha256"]
