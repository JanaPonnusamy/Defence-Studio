"""Real forced-alignment adapter: honest unavailability, tokenizer mapping,
metadata validation, and — when torch is installed — the genuine
``torchaudio.functional.forced_align`` integration.

The torch-gated tests drive the REAL forced-alignment API with a *controlled*
emission tensor (a stand-in for an acoustic model's frame log-probabilities).
They verify the alignment math, word grouping, frame→second conversion, and
ordering against the real library. They are **NOT** a full real-acoustic-model
run and do not claim verified real Tamil/English speech alignment — that
requires an operator-provisioned model (see README "Enabling real alignment").
No test performs a network download or needs a model file.
"""
from __future__ import annotations

import json
import threading

import pytest

from align_engine.engines.base import EngineTokenInput
from align_engine.engines.real_adapter import RealForcedAligner
from align_engine.errors import UnsupportedCapabilityError

# Tiny controlled vocabulary: blank=0, a..d and two Tamil codepoints.
_DICT = {"a": 1, "b": 2, "c": 3, "d": 4, "க": 5, "ா": 6}
_BLANK = 0


def _inp(word: str, i: int, segment_id: str = "seg") -> EngineTokenInput:
    return EngineTokenInput(
        token_id=f"{segment_id}:t{i}", original_text=word,
        normalized_text=word, segment_id=segment_id, index=i,
    )


# --------------------------------------------------------------------------- #
# Availability / honesty (no model, no torch required for these branches)
# --------------------------------------------------------------------------- #

def test_declared_languages_are_not_tested_languages():
    caps = RealForcedAligner().capabilities()
    assert caps.is_synthetic is False
    assert caps.declared_languages == ("en", "ta")
    assert caps.tested_languages == ()   # accepting a code is not a tested capability
    assert caps.requires_model is True


def test_unavailable_when_backend_missing(monkeypatch):
    engine = RealForcedAligner(model_path="")
    monkeypatch.setattr(engine, "_backend_present", lambda: False)
    caps = engine.capabilities()
    assert caps.available is False
    assert "torch" in caps.notes


def test_unavailable_when_model_missing(monkeypatch, tmp_path):
    engine = RealForcedAligner(model_path=str(tmp_path / "nope.pt"))
    monkeypatch.setattr(engine, "_backend_present", lambda: True)
    caps = engine.capabilities()
    assert caps.available is False
    assert "model" in caps.notes.lower()


def test_align_raises_when_unavailable():
    engine = RealForcedAligner(model_path="")
    with pytest.raises(UnsupportedCapabilityError):
        engine.align(audio_path="x", duration_seconds=1.0, language="en",
                     tokens=[_inp("hi", 0)], requested_granularity="word")


def test_align_honours_cancel_before_any_work(monkeypatch):
    engine = RealForcedAligner(model_path="")
    monkeypatch.setattr(engine, "_available", lambda: True)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(UnsupportedCapabilityError) as exc:
        engine.align(audio_path="x", duration_seconds=1.0, language="en",
                     tokens=[_inp("hi", 0)], requested_granularity="word", cancel=cancel)
    assert "cancel" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# Tokenizer mapping + metadata validation (pure, no torch)
# --------------------------------------------------------------------------- #

def test_word_token_ids_maps_known_and_drops_oov_and_blank():
    engine = RealForcedAligner()
    assert engine._word_token_ids("abc", _DICT, _BLANK) == [1, 2, 3]
    assert engine._word_token_ids("azb", _DICT, _BLANK) == [1, 2]       # 'z' OOV dropped
    # A token id equal to the CTC blank is never emitted as a target.
    assert engine._word_token_ids("a", {"a": _BLANK}, _BLANK) == []


def test_validate_meta_rejects_malformed():
    v = RealForcedAligner._validate_meta
    with pytest.raises(UnsupportedCapabilityError):
        v({"dictionary": {}, "sample_rate": 16000})          # empty dictionary
    with pytest.raises(UnsupportedCapabilityError):
        v({"dictionary": {"a": 1}, "sample_rate": 0})        # bad sample rate
    with pytest.raises(UnsupportedCapabilityError):
        v("not-a-dict")
    good = v({"dictionary": {"a": 1}, "sample_rate": 16000, "blank_id": 0})
    assert good["sample_rate"] == 16000 and good["dictionary"] == {"a": 1}


def test_load_bundle_fails_fast_on_missing_sidecar(tmp_path):
    model_file = tmp_path / "m.pt"
    model_file.write_bytes(b"not-a-real-model")   # sidecar deliberately absent
    engine = RealForcedAligner(model_path=str(model_file))
    with pytest.raises(UnsupportedCapabilityError) as exc:
        engine._load_model_bundle()               # validated before torch import
    assert "sidecar" in str(exc.value).lower()


def test_load_bundle_fails_on_malformed_sidecar(tmp_path):
    model_file = tmp_path / "m.pt"
    model_file.write_bytes(b"x")
    (tmp_path / "m.pt.meta.json").write_text(json.dumps({"dictionary": {}, "sample_rate": 16000}))
    engine = RealForcedAligner(model_path=str(model_file))
    with pytest.raises(UnsupportedCapabilityError):
        engine._load_model_bundle()


# --------------------------------------------------------------------------- #
# Real torchaudio forced-alignment integration (controlled emission)
# --------------------------------------------------------------------------- #

def _log_probs(plan: list[int], vocab: int):
    """Build frame log-probabilities whose argmax path follows ``plan``."""
    torch = pytest.importorskip("torch")
    emis = torch.full((len(plan), vocab), -8.0)
    for t, tok in enumerate(plan):
        emis[t, tok] = 0.0
    return torch.log_softmax(emis, dim=-1).unsqueeze(0)   # [1, T, C]


def test_align_tokens_uses_frame_evidence_not_even_division():
    pytest.importorskip("torch")
    engine = RealForcedAligner()
    # word1="ab" (ids 1,2) occupies frames 0-1; word2="cd" (ids 3,4) frames 2-7.
    plan = [1, 2, 3, 3, 3, 3, 4, 4]
    log_probs = _log_probs(plan, vocab=7)
    tokens = [_inp("ab", 0), _inp("cd", 1), _inp("zz", 2)]   # "zz" is OOV
    # duration 4.0s over 8 frames => 0.5s/frame.
    result = engine._align_tokens(
        log_probs=log_probs, token_inputs=tokens, dictionary=_DICT,
        blank_id=_BLANK, sec_per_frame=0.5,
    )
    assert result.actual_granularity == "word"
    by_id = {t.token_id: t for t in result.tokens}
    w1, w2, oov = by_id["seg:t0"], by_id["seg:t1"], by_id["seg:t2"]

    assert oov.aligned is False and "vocabulary" in oov.reason

    assert w1.aligned and w2.aligned
    assert w1.start == 0.0
    assert w1.start <= w2.start                      # ordered
    assert 0.0 <= w1.end <= w2.end <= 4.0            # within measured duration
    # Spans follow the acoustics (w2 much longer), NOT an equal split of 4.0s.
    d1, d2 = w1.end - w1.start, w2.end - w2.start
    assert d2 > d1 + 0.5
    assert abs(d1 - 2.0) > 0.1 and abs(d2 - 2.0) > 0.1   # not 2.0s each
    # Confidence is a genuine engine-provided probability, not invented.
    assert all(0.0 < t.confidence <= 1.0 for t in (w1, w2))


def test_align_full_path_with_injected_model(monkeypatch):
    torch = pytest.importorskip("torch")
    engine = RealForcedAligner(model_path="/provisioned/model.pt")
    meta = {"dictionary": _DICT, "sample_rate": 16000, "blank_id": _BLANK}
    log_probs = _log_probs([1, 2, 3, 4], vocab=7)
    monkeypatch.setattr(engine, "_available", lambda: True)
    monkeypatch.setattr(engine, "_load_model_bundle", lambda: (object(), meta))
    monkeypatch.setattr(engine, "_decode_audio", lambda path, sr: torch.zeros(1, 16000))
    monkeypatch.setattr(engine, "_emissions", lambda model, wav: log_probs)

    result = engine.align(audio_path="rel.wav", duration_seconds=2.0, language="en",
                          tokens=[_inp("ab", 0), _inp("cd", 1)], requested_granularity="word")
    aligned = [t for t in result.tokens if t.aligned]
    assert len(aligned) == 2
    starts = [t.start for t in aligned]
    assert starts == sorted(starts)
    assert all(0.0 <= t.start <= t.end <= 2.0 for t in aligned)


def test_align_tamil_tokens_map_and_align():
    pytest.importorskip("torch")
    engine = RealForcedAligner()
    # Tamil "கா" normalizes to codepoints 'க'(5) + 'ா'(6); prove the real path
    # produces a genuine span for Tamil input (declared, not a tested-accuracy claim).
    log_probs = _log_probs([5, 6], vocab=7)
    result = engine._align_tokens(
        log_probs=log_probs, token_inputs=[_inp("கா", 0)], dictionary=_DICT,
        blank_id=_BLANK, sec_per_frame=0.5,
    )
    tok = result.tokens[0]
    assert tok.aligned and tok.start is not None and tok.end >= tok.start


def test_align_raises_on_insufficient_frames():
    pytest.importorskip("torch")
    engine = RealForcedAligner()
    log_probs = _log_probs([1, 2], vocab=7)            # only 2 frames
    with pytest.raises(UnsupportedCapabilityError) as exc:
        engine._align_tokens(log_probs=log_probs, token_inputs=[_inp("abcd", 0)],
                             dictionary=_DICT, blank_id=_BLANK, sec_per_frame=0.5)
    assert "too short" in str(exc.value).lower() or "insufficient" in str(exc.value).lower()


def test_align_raises_when_all_tokens_out_of_vocabulary():
    pytest.importorskip("torch")
    engine = RealForcedAligner()
    log_probs = _log_probs([1, 2, 3, 4], vocab=7)
    with pytest.raises(UnsupportedCapabilityError) as exc:
        engine._align_tokens(log_probs=log_probs, token_inputs=[_inp("zzz", 0)],
                             dictionary=_DICT, blank_id=_BLANK, sec_per_frame=0.5)
    assert "vocabulary" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# Service boundary: a real (non-synthetic) engine needs no allow_synthetic flag
# and its timings are mapped back onto the exact original transcript text.
# --------------------------------------------------------------------------- #

class _FakeRealEngine:
    """A non-synthetic, available engine producing genuine frame-style spans.

    Stands in for RealForcedAligner at the service boundary so the mapping of
    engine output to original text (and the synthetic-vs-real distinction) is
    exercised without torch or a model.
    """
    name = "fake-real"
    version = "1"

    def capabilities(self):
        from align_engine.engines.base import EngineCapabilities
        return EngineCapabilities(
            engine_name="fake-real-aligner", engine_version="1", available=True,
            is_synthetic=False, declared_languages=("en", "ta"), tested_languages=(),
            audio_formats=("wav",), granularities=("word",), requires_model=True,
            notes="fake real engine for service mapping test",
        )

    def is_available(self):
        return True

    def align(self, *, audio_path, duration_seconds, language, tokens, requested_granularity, cancel=None):
        from align_engine.engines.base import EngineAlignResult, EngineTokenResult
        n = max(1, len(tokens))
        out = []
        for i, ti in enumerate(tokens):
            start = round(duration_seconds * i / n, 3)
            end = round(duration_seconds * (i + 1) / n, 3)
            out.append(EngineTokenResult(token_id=ti.token_id, aligned=True, start=start,
                                         end=end, confidence=0.9, kind="word"))
        return EngineAlignResult(actual_granularity="word", tokens=out)


def test_service_runs_real_engine_without_allow_synthetic_and_preserves_text(config):
    from align_engine.service import run_alignment
    from tests.conftest import build_request

    request = build_request(config, engine="real", allow_synthetic=False, language="en")
    output = run_alignment(request, _FakeRealEngine(), config)

    assert output["is_synthetic"] is False            # real engine, not test data
    assert output["validation"]["passed"] is True
    # Original transcript text is preserved verbatim (English terms/designations).
    joined = " ".join(
        tok["original_text"]
        for section in output["sections"]
        for segment in section["segments"]
        for tok in segment["tokens"]
    )
    assert "DRDO's" in joined and "Mach-2" in joined and "AESA" in joined
