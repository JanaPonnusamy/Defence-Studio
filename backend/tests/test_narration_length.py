"""Hard 900–1,300 spoken-narration gate (G4). Counts hook + narrations + closing only."""
from defence_studio.contracts.script import GenerationConfigV1, count_words, narration_word_count
from defence_studio.services.script_integrity import integrity_issues
from pydantic import ValidationError
import pytest


def _body(words, *, extra_meta=None):
    spoken = " ".join(f"w{i}" for i in range(words))
    body = {
        "opening_hook": spoken if words else "",
        "sections": [{"section_id": "s1", "heading": "Heading not counted " + "x " * 50,
                      "narration": "", "evidence": {"claim_ids": ["c1"], "source_ids": ["s"],
                                                    "qualification": "note " + "meta " * 40},
                      "presentation_note": "note " + "pad " * 40}],
        "closing": "",
        "config": {"target_words": 1100, "min_words": 900, "max_words": 1300},
        "word_count": {"words": extra_meta if extra_meta is not None else words},
        "usage": {"model": "x", "input_tokens": 99999, "output_tokens": 99999},
    }
    return body


def _pkg():
    return {"eligible_claim_ids": ["c1"], "claims": [], "sources": [], "duplicate_groups": []}


def _codes(body):
    return {i["code"]: i["severity"] for i in integrity_issues(body, _pkg())}


def test_count_words_whitespace_and_tamil():
    assert count_words("") == 0
    assert count_words("one two three") == 3
    assert count_words("ஒன்று இரண்டு மூன்று") == 3


def test_narration_excludes_headings_evidence_and_usage():
    body = _body(0)
    body["opening_hook"] = "alpha beta"
    body["sections"][0]["narration"] = "gamma"
    body["closing"] = "delta epsilon"
    assert narration_word_count(body) == 5


def test_empty_narration_fails():
    assert _codes(_body(0))["NARRATION_LENGTH_OUT_OF_RANGE"] == "ERROR"


@pytest.mark.parametrize("n,expect_error", [(899, True), (900, False), (1300, False), (1301, True)])
def test_range_boundaries(n, expect_error):
    codes = _codes(_body(n))
    if expect_error:
        assert codes["NARRATION_LENGTH_OUT_OF_RANGE"] == "ERROR"
    else:
        assert "NARRATION_LENGTH_OUT_OF_RANGE" not in codes


def test_metadata_cannot_inflate_the_spoken_count():
    body = _body(10, extra_meta=1200)
    assert narration_word_count(body) == 10
    assert _codes(body)["NARRATION_LENGTH_OUT_OF_RANGE"] == "ERROR"


def test_config_rejects_target_outside_min_max():
    with pytest.raises(ValidationError):
        GenerationConfigV1(
            language="en", audience="a", tone="t", script_format="narration",
            target_duration_seconds=300, target_words=800, min_words=900, max_words=1300,
            model="gpt-4o-mini", prompt_version="defence-studio.source-grounded-script.v1",
            max_text_cost_inr=1.0, terminology={},
        )
