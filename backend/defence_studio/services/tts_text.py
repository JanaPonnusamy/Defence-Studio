"""Deterministic Tamil text preparation for TTS (docs/TAMIL_SCRIPT_RULES.md sections 4-6 and 9).

Only the text sent to the speech engine changes; the stored narration and the
subtitles keep the approved script exactly. Every substitution comes from the
reviewed lexicon (``backend/config/tamil_tts_lexicon.json``), the military
designation table (``backend/config/tts_designations.json``: Su-57 -> "Su fifty
seven", spoken in English, hyphen not spoken) or a fixed unit rule, and is recorded. Digits are left as written: both ta-IN neural voices read
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
DESIGNATIONS_PATH = LEXICON_PATH.with_name("tts_designations.json")
_TAMIL = "஀-௿"
# A designation-shaped token (letters, hyphen, digits, optional suffix) that is not in the table.
_DESIGNATION_LIKE = re.compile(r"(?<![A-Za-z0-9-])[A-Za-z]{1,5}-\d+[A-Za-z]*(?![A-Za-z0-9])")

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


def load_designations(path: Path = DESIGNATIONS_PATH) -> dict:
    return {k: v["spoken"] for k, v in json.loads(path.read_bytes())["designations"].items()}


def load_suffix_rewrites(path: Path = DESIGNATIONS_PATH) -> dict:
    return json.loads(path.read_bytes()).get("suffix_rewrites", {})


def _designation_pattern(term: str) -> re.Pattern:
    """Exact designation, case-insensitive, not inside a longer token. A Tamil case suffix may follow
    directly or after a hyphen (Su-57-ல், Su-57ல்); that joining hyphen is captured so it is not spoken."""
    return re.compile(r"(?<![A-Za-z0-9-])" + re.escape(term) + rf"(?:(-)(?=[{_TAMIL}])|(?=[{_TAMIL}])|(?![A-Za-z0-9-]))",
                      re.IGNORECASE)


def check_integrity(text: str) -> None:
    """Reject text whose Tamil words are broken (a vowel sign or pulli with no base letter)."""
    if text != unicodedata.normalize("NFC", text):
        raise TamilTextError("Text is not NFC-normalized")
    for token in text.split():
        if unicodedata.category(token[0]).startswith("M"):
            raise TamilTextError(f"Broken Tamil word (starts with a combining mark): {token!r}")


def prepare(text: str, language: str, lexicon: tuple[dict, str] | None = None,
            designations: dict | None = None, suffix_rewrites: dict | None = None) -> dict:
    text = unicodedata.normalize("NFC", text)
    if language != "ta":
        return {"spoken": text, "replacements": [], "warnings": [], "lexicon_sha256": None}
    check_integrity(text)
    terms, lexicon_sha = lexicon or load_lexicon()
    spoken, replacements = text, []
    designations = load_designations() if designations is None else designations
    designation_words, unreviewed_suffixes = set(), set()
    # Reviewed rewrites first: Su-57-ல் -> "Su fifty seven விமானத்தில்", so no bare suffix token is spoken.
    rewrites = load_suffix_rewrites() if suffix_rewrites is None else suffix_rewrites
    for form, rule in rewrites.items():
        if rule["designation"] not in designations:
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9-])" + re.escape(rule["designation"]) + "-?" + re.escape(rule["suffix"])
                             + rf"(?![{_TAMIL}])", re.IGNORECASE)
        spoken, n = pattern.subn(rule["spoken"], spoken)
        if n:
            replacements.append({"from": form, "to": rule["spoken"], "count": n,
                                 "source": "designation_suffix_rewrite"})
            designation_words.update(rule["spoken"].split())
    for term in sorted(designations, key=len, reverse=True):
        said = designations[term]

        def say(m, said=said):
            # An unreviewed Tamil suffix is kept as its own word after the English spoken form and reported.
            suffix = re.match(rf"[{_TAMIL}]*", m.string[m.end():]).group(0)
            if suffix:
                unreviewed_suffixes.add(m.group(0) + suffix)
            return said + " " if suffix else said
        spoken, n = _designation_pattern(term).subn(say, spoken)
        if n:
            replacements.append({"from": term, "to": said, "count": n, "source": "designation"})
            designation_words.update(said.split())
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
    unknown = sorted({m.group(0) for m in _LATIN_TERM.finditer(spoken)} - designation_words)
    warnings = [f"Latin term not in the verified lexicon (pronunciation unchecked): {t}" for t in unknown]
    warnings += [f"Designation not in the pronunciation table (left unchanged, needs review): {t}"
                 for t in sorted({m.group(0) for m in _DESIGNATION_LIKE.finditer(spoken)})]
    warnings += [f"Tamil suffix attached to a designation, needs a reviewed speech rewrite: {t}"
                 for t in sorted(unreviewed_suffixes)]
    return {"spoken": spoken, "replacements": replacements, "warnings": warnings, "lexicon_sha256": lexicon_sha}


__all__ = ["prepare", "check_integrity", "load_lexicon", "load_designations", "load_suffix_rewrites",
           "TamilTextError", "LEXICON_PATH", "DESIGNATIONS_PATH"]
