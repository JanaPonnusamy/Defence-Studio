"""Performance Planner.

Converts (script, performance_profile) into a semantic, provider-neutral
list of per-sentence performance instructions (spec section 11). This
contains no provider-specific knowledge -- TTS adapters translate these
instructions into provider-specific parameters.
"""
from __future__ import annotations

import re


def _split_sentences(script: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    return [s.strip() for s in sentences if s.strip()]


def _pick_emphasis_candidate(sentence: str) -> str | None:
    """Naive emphasis heuristic: the longest capitalized-looking word or the
    longest word overall, used only as a semantic hint for the planner --
    not a measurement."""
    words = re.findall(r"[A-Za-z']+", sentence)
    if not words:
        return None
    return max(words, key=len)


def build_performance_plan(script: str, profile: dict, language: str = "en") -> list[dict]:
    sentences = _split_sentences(script)
    tempo = profile.get("tempo", "moderate")
    pause_style = profile.get("pause_style", "medium")
    energy = profile.get("energy", "controlled-high")
    emphasis_mode = profile.get("emphasis", "selective")
    pitch_style = profile.get("pitch_style", "moderately dynamic")
    transition_pause = profile.get("transition_pause", "medium")

    instructions = []
    for i, sentence in enumerate(sentences, start=1):
        emphasis_word = _pick_emphasis_candidate(sentence) if emphasis_mode != "minimal" else None
        instructions.append({
            "sentence_index": i,
            "text": sentence,
            "tone": "authoritative" if energy in ("controlled-high", "high") else "measured",
            "pace": tempo,
            "emphasis": emphasis_word,
            "pause_after": pause_style if i < len(sentences) else transition_pause,
            "energy": energy,
            "pitch_style": pitch_style,
            "language": language,
        })
    return instructions
