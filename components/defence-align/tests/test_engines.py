"""Engine capabilities + deterministic/real aligner behavior."""
import pytest

from align_engine.engines import (
    DeterministicTestAligner, EngineTokenInput, RealForcedAligner, default_registry,
)
from align_engine.errors import UnsupportedCapabilityError


def _inputs(words, segment_id="seg"):
    return [EngineTokenInput(token_id=f"{segment_id}:t{i}", original_text=w, normalized_text=w.lower(),
                             segment_id=segment_id, index=i) for i, w in enumerate(words)]


def test_deterministic_is_synthetic_and_available():
    caps = DeterministicTestAligner().capabilities()
    assert caps.is_synthetic is True and caps.available is True
    assert "deterministic-test-aligner" == caps.engine_name


def test_deterministic_word_timings_ordered_within_duration():
    engine = DeterministicTestAligner()
    result = engine.align(audio_path="x", duration_seconds=3.0, language="en",
                          tokens=_inputs(["one", "two", "three"]), requested_granularity="word")
    assert result.actual_granularity == "word"
    starts = [t.start for t in result.tokens]
    assert starts == sorted(starts)                      # ordered
    assert all(0 <= t.start <= t.end <= 3.0 for t in result.tokens)  # within duration
    assert all(t.confidence is None for t in result.tokens)  # never invents confidence
    # Length-weighted, not even division: a longer token gets a longer span.
    spans = {t.token_id: t.end - t.start for t in result.tokens}
    assert spans["seg:t2"] > spans["seg:t0"]             # "three" longer than "one"


def test_deterministic_sentence_granularity_is_labelled():
    engine = DeterministicTestAligner()
    toks = _inputs(["a", "b"], "segA") + _inputs(["c"], "segB")
    result = engine.align(audio_path="x", duration_seconds=2.0, language="en",
                          tokens=toks, requested_granularity="sentence")
    assert result.actual_granularity == "sentence"
    assert {t.kind for t in result.tokens} == {"segment"}
    assert {t.token_id for t in result.tokens} == {"segA", "segB"}


def test_real_adapter_unavailable_and_honest():
    engine = RealForcedAligner()
    caps = engine.capabilities()
    assert caps.available is False and caps.is_synthetic is False
    # Declared support is not tested capability.
    assert "ta" in caps.declared_languages and caps.tested_languages == ()
    with pytest.raises(UnsupportedCapabilityError):
        engine.align(audio_path="x", duration_seconds=1.0, language="en",
                     tokens=_inputs(["hi"]), requested_granularity="word")


def test_registry_lists_and_resolves_engines():
    reg = default_registry()
    assert set(reg.names()) == {"deterministic-test", "real"}
    assert reg.get("deterministic-test").capabilities().is_synthetic
    with pytest.raises(UnsupportedCapabilityError):
        reg.get("nope")
