"""Tokenization: Tamil Unicode, English technical terms, punctuation, spans."""
from align_engine.normalize import tokenize_segment


def _reconstruct(original, tokens):
    # Every token's original_text must be the exact substring at its span.
    for t in tokens:
        assert original[t.char_start:t.char_end] == t.original_text
    return tokens


def test_english_preserves_technical_terms_and_contractions():
    text = "The DRDO's Rafale reached Mach-2 (AESA)."
    tokens = _reconstruct(text, tokenize_segment("seg", text))
    words = [t.original_text for t in tokens if t.kind == "word"]
    assert "DRDO's" in words        # contraction kept as one word
    assert "Mach-2" in words         # hyphenated designation kept as one word
    assert "AESA" in words           # acronym preserved verbatim
    puncts = [t.original_text for t in tokens if t.kind == "punctuation"]
    assert "(" in puncts and ")." in puncts  # punctuation preserved, not spoken words


def test_punctuation_is_not_a_spoken_word():
    tokens = tokenize_segment("seg", "Hello, world!")
    kinds = {t.original_text: t.kind for t in tokens}
    assert kinds["Hello"] == "word" and kinds["world"] == "word"
    assert kinds[","] == "punctuation" and kinds["!"] == "punctuation"


def test_tamil_unicode_preserved_with_embedded_english():
    text = "ராஃபேல் விமானம் DRDO ஆல் சோதிக்கப்பட்டது."
    tokens = _reconstruct(text, tokenize_segment("seg", text))
    words = [t.original_text for t in tokens if t.kind == "word"]
    assert "ராஃபேல்" in words                 # Tamil word preserved, not transliterated
    assert "DRDO" in words                      # embedded English acronym preserved
    # No token loses or rewrites Tamil codepoints.
    assert "".join(t.original_text for t in tokens if t.kind == "word") \
        .count("விமானம்") == 1


def test_normalized_form_is_lossless_to_original():
    text = "AESA Radar"
    tokens = tokenize_segment("seg", text)
    word = next(t for t in tokens if t.original_text == "AESA")
    assert word.normalized_text == "aesa"       # normalized (casefold) ...
    assert word.original_text == "AESA"          # ... original preserved verbatim
