"""Provider-independent Voice Direction contract + validation."""
from __future__ import annotations
import hashlib, json

EMOTIONS = {"CALM", "SERIOUS", "URGENT", "DRAMATIC", "CONFIDENT", "CONCERNED", "NEUTRAL"}
PAUSE_POSITIONS = {"before_speech", "after_sentence", "before_emphasis", "after_emphasis"}
TEMPO_MIN, TEMPO_MAX = 0.5, 2.0
PAUSE_MAX_MS = 5000
MAX_SCRIPT_CHARS = 20000


class DirectionError(ValueError):
    code = "INVALID_DIRECTION"


def _num(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise DirectionError(f"{name} must be a number")
    if not lo <= v <= hi:
        raise DirectionError(f"{name} must be within [{lo}, {hi}]")
    return float(v)


def validate_direction(d: dict) -> dict:
    """Return a normalized copy with defaults applied; raise DirectionError on any invalid field."""
    if not isinstance(d, dict):
        raise DirectionError("direction must be an object")
    for k in ("voice_id", "language"):
        if not d.get(k) or not isinstance(d[k], str):
            raise DirectionError(f"{k} is required")
    out = {"voice_id": d["voice_id"], "language": d["language"], "style_profile_id": d.get("style_profile_id")}
    emo = d.get("emotion") or {"label": "NEUTRAL", "intensity": 0.5}
    if emo.get("label") not in EMOTIONS:
        raise DirectionError(f"emotion.label must be one of {sorted(EMOTIONS)}")
    out["emotion"] = {"label": emo["label"], "intensity": _num(emo.get("intensity", 0.5), "emotion.intensity", 0, 1)}
    out["tempo"] = _num(d.get("tempo", 1.0), "tempo", TEMPO_MIN, TEMPO_MAX)
    out["pitch_variation"] = _num(d.get("pitch_variation", 0.5), "pitch_variation", 0, 1)
    out["energy"] = _num(d.get("energy", 0.7), "energy", 0, 1)
    out["emphasis"] = []
    for e in d.get("emphasis") or []:
        if not isinstance(e, dict) or not str(e.get("text", "")).strip():
            raise DirectionError("emphasis.text is required")
        out["emphasis"].append({"text": e["text"].strip(), "strength": _num(e.get("strength", 0.5), "emphasis.strength", 0, 1)})
    out["pauses"] = []
    for p in d.get("pauses") or []:
        if p.get("position") not in PAUSE_POSITIONS:
            raise DirectionError(f"pause.position must be one of {sorted(PAUSE_POSITIONS)}")
        out["pauses"].append({"position": p["position"],
                              "duration_ms": int(_num(p.get("duration_ms"), "pause.duration_ms", 0, PAUSE_MAX_MS))})
    return out


def canonical(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(s: str | bytes) -> str:
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()
