"""Generate a runnable synthetic sample (WAV + hash-correct request JSON).

Offline only: writes a silent PCM WAV with the stdlib and fills in the matching
``audio_sha256`` / ``transcript_sha256``. Run:

    python samples/make_sample.py --out-dir ./_sample_run

Then point the API/CLI at the generated input root.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

_SAMPLES = Path(__file__).resolve().parent
# Allow running as a plain script (python samples/make_sample.py) by putting the
# package root on sys.path.
sys.path.insert(0, str(_SAMPLES.parent))

from align_engine._hashing import digest, sha256_file  # noqa: E402
from align_engine.contracts import REQUEST_SCHEMA  # noqa: E402


def _make_wav(path: Path, seconds: float = 2.0, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))


def _transcript_sha(request: dict) -> str:
    body = [
        {"section_id": s["section_id"], "segments": [
            {"segment_id": seg["segment_id"], "original_text": seg["original_text"],
             "normalized_text": seg.get("normalized_text"), "pronunciation": seg.get("pronunciation")}
            for seg in s["segments"]]}
        for s in request["sections"]
    ]
    return digest({"schema_version": REQUEST_SCHEMA, "language": request["language"], "sections": body})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, help="input root to populate")
    parser.add_argument("--language", choices=("en", "ta"), default="en")
    args = parser.parse_args()
    out = Path(args.out_dir).resolve()
    template = json.loads((_SAMPLES / f"request_{args.language}.json").read_text(encoding="utf-8"))

    audio_rel = template["audio_path"]
    audio_abs = out / audio_rel
    _make_wav(audio_abs)
    template["audio_sha256"] = sha256_file(audio_abs)
    template["transcript_sha256"] = _transcript_sha(template)

    request_path = out / f"request_{args.language}.json"
    request_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {audio_abs}")
    print(f"Wrote {request_path}")
    print("Set DEFENCE_ALIGN_INPUT_ROOT to:", out)


if __name__ == "__main__":
    main()
