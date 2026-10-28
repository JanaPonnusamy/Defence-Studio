"""Deterministic Tamil text preparation for TTS (docs/TAMIL_SCRIPT_RULES.md sections 4-6 and 9).

Only the text sent to the speech engine changes; the stored narration and the
subtitles keep the approved script exactly. Every substitution comes from the
reviewed lexicon (``backend/config/tamil_tts_lexicon.json``) or a fixed unit
rule, and is recorded. Digits are left as written: both ta-IN neural voices read
years and Indian-grouped amounts (2,12,000) correctly in an ASR round-trip.
Unknown Latin acronyms are never transliterated automatically; they are reported.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

LEXICON_PATH = Path(__file__).resolve().parents[2] / "config" / "tamil_tts_lexicon.json"

# (pattern, replacement, label): unit and abbreviation rules that need no per-voice check.
_RULES = [
    (re.compile(r"(\d)\s*%"), r"\1 சதவீதம்", "percent"),
    (re.compile(r"(\d)\s*km\b"), r"\1 கிலோமீட்டர்", "km"),
    (re.compile(r"(?<!\w)(?:ரூ|Rs)\.\s*"), "ரூபாய் ", "rupee_abbreviation"),
]
_LATIN_TERM = re.compile(r"(?<![\w-])[A-Z][A-Za-z]*(?:-\d+)?[A-Z\d]*(?![\w-])")


class TamilTextError(ValueError):
    pass


def load_lexicon(path: Path = LEXICON_PATH) -> tuple[dict, str]:
    raw = path.read_bytes()
    terms = {k: v["spoken"] for k, v in json.loads(raw)["terms"].items()}
    return terms, hashlib.sha256(raw).hexdigest()


def check_integrity(text: str) -> None:
    """Reject text whose Tamil words are broken (a vowel sign or pulli with no base letter)."""
    if text != unicodedata.normalize("NFC", text):
        raise TamilTextError("Text is not NFC-normalized")
    for token in text.split():
        if unicodedata.category(token[0]).startswith("M"):
            raise TamilTextError(f"Broken Tamil word (starts with a combining mark): {token!r}")


def prepare(text: str, language: str, lexicon: tuple[dict, str] | None = None) -> dict:
    text = unicodedata.normalize("NFC", text)
    if language != "ta":
        return {"spoken": text, "replacements": [], "warnings": [], "lexicon_sha256": None}
    check_integrity(text)
    terms, lexicon_sha = lexicon or load_lexicon()
    spoken, replacements = text, []
    for term in sorted(terms, key=len, reverse=True):
        pattern = re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])")
        spoken, n = pattern.subn(terms[term], spoken)
        if n:
            replacements.append({"from": term, "to": terms[term], "count": n, "source": "lexicon"})
    for pattern, repl, label in _RULES:
        spoken, n = pattern.subn(repl, spoken)
        if n:
            replacements.append({"rule": label, "count": n, "source": "rule"})
    spoken = re.sub(r"[ \t]+", " ", spoken).strip()
    check_integrity(spoken)
    unknown = sorted({m.group(0) for m in _LATIN_TERM.finditer(spoken)})
    warnings = [f"Latin term not in the verified lexicon (pronunciation unchecked): {t}" for t in unknown]
    return {"spoken": spoken, "replacements": replacements, "warnings": warnings, "lexicon_sha256": lexicon_sha}


__all__ = ["prepare", "check_integrity", "load_lexicon", "TamilTextError", "LEXICON_PATH"]
