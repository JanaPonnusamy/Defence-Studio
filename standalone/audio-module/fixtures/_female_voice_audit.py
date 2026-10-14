"""One-off audit script (not part of the pytest suite) used during the
admin self-test to synthesize real audio with the explicitly-female SAPI5
voices found on this machine, via the module's own real local-pyttsx3
adapter code path, and compute objective signal statistics. Run with the
project's own venv. Not imported by the app; safe to delete afterward."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import soundfile as sf
import librosa

from app.tts.local_pyttsx3 import LocalPyttsx3Adapter
from app.tts.base import TTSRequest
from app.pipeline.align import detect_speech_regions
from app.pipeline.validate import validate_generated_audio

OUT_DIR = Path(__file__).resolve().parent / "_female_voice_audit_out"
OUT_DIR.mkdir(exist_ok=True)

VOICES = [
    ("Microsoft Zira Desktop - English (United States)", "zira"),
    ("Microsoft Hazel Desktop - English (Great Britain)", "hazel"),
]

PASSAGES = {
    "news_short": (
        "Authorities confirmed this morning that the regional transit authority "
        "will begin emergency repairs on the main bridge starting Monday."
    ),
    "tamil_attempt": "வຳளம் குழந்தை",  # will be attempted against an English voice to show it is NOT real Tamil synthesis
    "longer_passage": (
        "This is a longer passage intended to check consistency of pacing, pause "
        "placement, and pronunciation across multiple sentences. It contains several "
        "clauses, a numbered list such as one, two, and three, and a question: does the "
        "engine maintain a steady cadence throughout? Finally, this closing sentence "
        "checks that the synthesis does not degrade or clip toward the end of a longer run."
    ),
}


def objective_stats(path: Path, expected_text: str) -> dict:
    y, sr = librosa.load(str(path), sr=None, mono=True)
    duration = len(y) / sr if sr else 0.0
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
    rms_dbfs_proxy = 20 * np.log10(rms) if rms > 0 else -120.0

    regions = detect_speech_regions(y, sr)
    speech_time = sum(e - s for s, e in regions)
    voiced_frame_ratio = speech_time / duration if duration > 0 else 0.0

    f0, _, _ = librosa.pyin(y, fmin=60, fmax=500, sr=sr) if y.size >= 2048 else (np.array([]), None, None)
    f0_valid = f0[~np.isnan(f0)] if f0.size else np.array([])
    f0_mean = float(np.mean(f0_valid)) if f0_valid.size else None

    word_count = len(expected_text.split())
    expected_min_sec = word_count / 4.0  # ~240 wpm upper bound sanity floor
    expected_max_sec = word_count / 0.8  # ~48 wpm lower bound sanity ceiling
    duration_plausible = expected_min_sec <= duration <= expected_max_sec if word_count else None

    validation = validate_generated_audio(path)

    return {
        "duration_sec": round(duration, 3),
        "sample_rate": sr,
        "peak": round(peak, 4),
        "clipping": peak >= 0.999,
        "rms": round(rms, 5),
        "rms_dbfs_proxy": round(rms_dbfs_proxy, 2),
        "voiced_frame_ratio": round(voiced_frame_ratio, 3),
        "f0_mean_hz": round(f0_mean, 1) if f0_mean else None,
        "word_count_in_text": word_count,
        "duration_vs_wordcount_plausible": duration_plausible,
        "validation_status": validation["status"],
    }


def main():
    adapter = LocalPyttsx3Adapter()
    results = []
    for voice_name, short in VOICES:
        for passage_key, text in PASSAGES.items():
            dest = OUT_DIR / f"{short}_{passage_key}.wav"
            req = TTSRequest(text=text, voice_id=voice_name, language="en" if passage_key != "tamil_attempt" else "ta")
            result = adapter.synthesize(req, dest)
            stats = objective_stats(dest, text)
            entry = {
                "voice_name": voice_name,
                "passage": passage_key,
                "requested_language": req.language,
                "reported_voice": result.voice,
                "file": str(dest),
                **stats,
            }
            results.append(entry)
            print(entry)
    return results


if __name__ == "__main__":
    main()
