"""Tamil narration quality: deterministic TTS text preparation, measured sentence timing for
subtitles, the Tamil default provider, and the opt-in Tamil explainer prompt style."""
import pytest

from defence_studio.integrations import subtitles as subs
from defence_studio.services import tts_text
from defence_studio.services.audio import AudioService, DEFAULT_PROVIDERS
from defence_studio.services.script_prompt import system_prompt

LEX = ({"DRDO": "டிஆர்டிஓ", "S-400": "எஸ்-400"}, "sha")


def test_prepare_applies_lexicon_and_units_and_keeps_digits():
    out = tts_text.prepare("இதை DRDO உருவாக்கியது. S-400 அமைப்பு 25 km வரை, 96% உள்நாட்டு. ரூ. 2,12,000 கோடி.",
                           "ta", LEX)
    assert out["spoken"] == ("இதை டிஆர்டிஓ உருவாக்கியது. எஸ்-400 அமைப்பு 25 கிலோமீட்டர் வரை, "
                             "96 சதவீதம் உள்நாட்டு. ரூபாய் 2,12,000 கோடி.")
    assert {r.get("from") or r.get("rule") for r in out["replacements"]} == {"DRDO", "S-400", "km", "percent",
                                                                            "rupee_abbreviation"}
    assert out["warnings"] == []


def test_prepare_reports_unverified_latin_terms_without_changing_them():
    out = tts_text.prepare("இது MRSAM அமைப்பு.", "ta", LEX)
    assert out["spoken"] == "இது MRSAM அமைப்பு."
    assert out["warnings"] and "MRSAM" in out["warnings"][0]


def test_prepare_rejects_broken_tamil_words_and_leaves_english_alone():
    with pytest.raises(tts_text.TamilTextError):
        tts_text.prepare("ஆகாஷ் \u0bbeஏவுகணை", "ta", LEX)
    assert tts_text.prepare("DRDO built it.", "en", LEX)["spoken"] == "DRDO built it."


def test_repo_lexicon_loads():
    terms, sha = tts_text.load_lexicon()
    assert terms["DRDO"] == "டிஆர்டிஓ" and len(sha) == 64


def test_sentence_timing_pairs_only_matching_monotonic_boundaries():
    text = "முதல் வாக்கியம். இரண்டாவது வாக்கியம்?"
    good = [{"start": 0.1, "end": 1.5}, {"start": 1.7, "end": 3.0}]
    timing = AudioService._sentence_timing(text, good, 3.2)
    assert timing["sentences"][1] == {"text": "இரண்டாவது வாக்கியம்?", "start": 1.7, "end": 3.0}
    assert AudioService._sentence_timing(text, good[:1], 3.2) is None
    assert AudioService._sentence_timing(text, list(reversed(good)), 3.2) is None
    assert AudioService._sentence_timing(text, None, 3.2) is None


def test_cues_follow_measured_sentence_starts():
    scene = {"scene_id": "s1", "text": "முதல் வாக்கியம். இரண்டாவது வாக்கியம்.", "start": 10.0, "duration": 6.0,
             "sentence_timing": {"sentences": [{"text": "முதல் வாக்கியம்.", "start": 0.1, "end": 1.0},
                                               {"text": "இரண்டாவது வாக்கியம்.", "start": 4.0, "end": 5.8}]}}
    cues = subs.build_cues([scene])
    assert [(c["start"], c["end"], c["text"]) for c in cues] == [
        (10.0, 14.0, "முதல் வாக்கியம்."), (14.0, 16.0, "இரண்டாவது வாக்கியம்.")]
    assert subs.check_cues(cues, [scene], 16.0) == []
    # Without measured timing the proportional estimate is unchanged.
    plain = subs.build_cues([{**scene, "sentence_timing": None}])
    assert plain[1]["start"] != 14.0


def test_tamil_defaults_to_neural_provider():
    assert DEFAULT_PROVIDERS["ta"] == "edge-neural"


def test_explainer_style_is_tamil_and_opt_in():
    assert "YouTube defence presenter" in system_prompt("ta", "explainer")
    assert "YouTube defence presenter" not in system_prompt("ta", "narration")
    assert "YouTube defence presenter" not in system_prompt("en", "explainer")
