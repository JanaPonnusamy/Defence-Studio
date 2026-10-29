"""Military designations are spoken in English for Tamil TTS; canonical text (subtitles) is untouched."""
from defence_studio.integrations import subtitles as subs
from defence_studio.services import tts_text

LEX = ({"DRDO": "டிஆர்டிஓ"}, "sha")


def spoken(text):
    return tts_text.prepare(text, "ta", LEX)["spoken"]


def test_repo_table_has_the_required_entries():
    table = tts_text.load_designations()
    assert table["Su-57"] == "Su fifty seven" and table["S-400"] == "S four hundred"
    assert table["Su-30MKI"] == "Su thirty MKI" and table["MiG-29"] == "MiG twenty nine"
    assert table["S-300"] == "S three hundred" and table["F-35"] == "F thirty five"
    assert not any("-" in s or any(c.isdigit() for c in s) for s in table.values())


def test_designations_in_tamil_sentences_are_spoken_in_english():
    out = tts_text.prepare("ரஷ்யாவின் Su-57 மற்றும் S-400 பற்றி பார்க்கலாம்.", "ta", LEX)
    assert out["spoken"] == "ரஷ்யாவின் Su fifty seven மற்றும் S four hundred பற்றி பார்க்கலாம்."
    assert {r["from"] for r in out["replacements"] if r["source"] == "designation"} == {"Su-57", "S-400"}
    assert out["warnings"] == []


def test_suffixes_and_longer_designations():
    assert spoken("இந்தியாவின் Su-30MKI விமானம்.") == "இந்தியாவின் Su thirty MKI விமானம்."
    assert spoken("Su-57E என்ற மாடல்.") == "Su fifty seven E என்ற மாடல்."
    # An unreviewed Tamil suffix: the hyphen is not spoken, the suffix stays separate and is reported.
    out = tts_text.prepare("Su-57ஐ பார்த்தோம்.", "ta", LEX)
    assert out["spoken"] == "Su fifty seven ஐ பார்த்தோம்."
    assert out["warnings"] == ["Tamil suffix attached to a designation, needs a reviewed speech rewrite: Su-57ஐ"]


def test_su57_sample_sentence_suffix_is_rewritten_for_speech_only():
    # Su-57 sample script A, sentence 13 (canonical text).
    canonical = "Product 177 என்ற புதிய இன்ஜின், 2025 டிசம்பரில் Su-57-ல் முதன்முதலாகப் பறந்தது."
    out = tts_text.prepare(canonical, "ta", LEX)
    assert out["spoken"] == ("Product 177 என்ற புதிய இன்ஜின், 2025 டிசம்பரில் Su fifty seven விமானத்தில் "
                             "முதன்முதலாகப் பறந்தது.")
    assert "ல்" not in out["spoken"].split() and "Su fifty seven" in out["spoken"]
    assert {"from": "Su-57-ல்", "to": "Su fifty seven விமானத்தில்", "count": 1,
            "source": "designation_suffix_rewrite"} in out["replacements"]
    assert not any("suffix" in w for w in out["warnings"])
    # Same rewrite without the joining hyphen; the canonical subtitle text is unchanged.
    assert spoken("டிசம்பரில் Su-57ல் பறந்தது.") == "டிசம்பரில் Su fifty seven விமானத்தில் பறந்தது."
    cues = subs.build_cues([{"scene_id": "s13", "text": canonical, "start": 0.0, "duration": 7.0}])
    assert " ".join(c["text"] for c in cues) == canonical
    assert spoken(out["spoken"]) == out["spoken"]
    # Disabled with the designation table (the "before" comparison), and not applied to a longer Tamil word.
    assert tts_text.prepare(canonical, "ta", LEX, designations={})["spoken"] == canonical
    assert spoken("Su-57-லிருந்து பறந்தது.") == "Su fifty seven லிருந்து பறந்தது."


def test_case_insensitive_match_and_canonical_text_unchanged():
    narration = "su-57 மற்றும் mig-29 விமானங்கள்."
    assert spoken(narration) == "Su fifty seven மற்றும் MiG twenty nine விமானங்கள்."
    canonical = "ரஷ்யாவின் Su-57 மற்றும் S-400."
    tts_text.prepare(canonical, "ta", LEX)
    cues = subs.build_cues([{"scene_id": "s1", "text": canonical, "start": 0.0, "duration": 4.0}])
    assert " ".join(c["text"] for c in cues) == canonical


def test_minus_signs_and_unrelated_hyphens_are_untouched():
    for text in ("வெப்பநிலை -5 டிகிரி.", "10 - 3 = 7 ஆகும்.", "மதிப்பு −5 ஆகும்.", "XS-400 என்ற பெயர்.",
                 "S-4000 என்ற எண்."):
        assert spoken(text) == text


def test_ordinary_tamil_is_unchanged_and_processing_is_idempotent():
    plain = "இந்த விமானம் 2010 ஜனவரி 29-ஆம் தேதி பறந்தது."
    assert spoken(plain) == plain
    once = spoken("Su-57 மற்றும் S-400.")
    assert spoken(once) == once == "Su fifty seven மற்றும் S four hundred."


def test_unknown_designations_are_reported_not_guessed():
    out = tts_text.prepare("Tu-160 மற்றும் J-20 விமானங்கள்.", "ta", LEX)
    assert out["spoken"] == "Tu-160 மற்றும் J-20 விமானங்கள்."
    assert [w for w in out["warnings"] if w.startswith("Designation not in")] == [
        "Designation not in the pronunciation table (left unchanged, needs review): J-20",
        "Designation not in the pronunciation table (left unchanged, needs review): Tu-160"]


def test_english_is_untouched():
    assert tts_text.prepare("The Su-57 flew.", "en", LEX)["spoken"] == "The Su-57 flew."
