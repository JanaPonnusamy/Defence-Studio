"""Deterministic, lossless-to-original tokenization for Tamil and English.

Every token keeps its exact original substring and its ``[char_start,
char_end)`` offsets into the segment's original text, so the full original is
always reconstructable and every normalization decision is traceable.

- A ``word`` token is a maximal run of Unicode letters/digits (Tamil block
  U+0B80-U+0BFF, Latin letters, digits) plus word-internal apostrophes and
  hyphens (English contractions/hyphenation). Tamil combining marks stay
  attached to their base.
- A ``punctuation`` token is any other non-whitespace run. Punctuation is
  preserved but is NEVER treated as a spoken word.
- Whitespace is a separator and is not emitted as a token.

Normalization produces a ``normalized_text`` via Unicode NFC + casefold. This
is non-destructive: the original is always retained separately, Tamil Unicode
is composed (not transliterated — no romanization, no English terms dropped),
and English acronyms/technical terms survive verbatim in ``original_text``.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Tamil block plus Latin letters and decimal digits count as word characters.
# Word-internal ' and - join a single word (don't split "DRDO's" or "Mach-2").
_TAMIL_START, _TAMIL_END = 0x0B80, 0x0BFF
_WORD_JOINERS = {"'", "’", "-"}


def _is_word_char(ch: str) -> bool:
    if ch.isalnum():
        return True
    code = ord(ch)
    if _TAMIL_START <= code <= _TAMIL_END:
        return True
    # Combining marks (e.g. Tamil vowel signs) extend the current word.
    return unicodedata.category(ch).startswith("M")


@dataclass(frozen=True)
class Token:
    token_id: str
    kind: str          # "word" | "punctuation"
    original_text: str
    normalized_text: str
    char_start: int
    char_end: int


def normalize_token(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def tokenize_segment(segment_id: str, original_text: str) -> list[Token]:
    """Tokenize one segment's original text into ordered word/punctuation tokens
    with stable, deterministic token IDs (``<segment_id>:t<index>``)."""
    tokens: list[Token] = []
    i, n = 0, len(original_text)
    index = 0
    while i < n:
        ch = original_text[i]
        if ch.isspace():
            i += 1
            continue
        start = i
        if _is_word_char(ch):
            i += 1
            while i < n:
                cur = original_text[i]
                if _is_word_char(cur):
                    i += 1
                elif cur in _WORD_JOINERS and i + 1 < n and _is_word_char(original_text[i + 1]):
                    i += 1  # word-internal joiner only when flanked by word chars
                else:
                    break
            kind = "word"
        else:
            i += 1
            while i < n and not original_text[i].isspace() and not _is_word_char(original_text[i]) \
                    and original_text[i] not in _WORD_JOINERS:
                i += 1
            kind = "punctuation"
        chunk = original_text[start:i]
        tokens.append(Token(
            token_id=f"{segment_id}:t{index}", kind=kind, original_text=chunk,
            normalized_text=normalize_token(chunk), char_start=start, char_end=i,
        ))
        index += 1
    return tokens
