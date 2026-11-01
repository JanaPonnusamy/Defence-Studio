"""Frame-level and phrase-level delivery (prosody) measurement.

Reuses the EXT-03 analyzer's autocorrelation pitch tracker and frame grid (16 kHz,
10 ms hop) so Voice Lab and the existing Voice tab measure pitch identically.
Everything here is a *delivery* description: relative pitch movement (semitones
around the speaker's own median), timing, pauses, energy and emphasis. Nothing
here is or produces a speaker-identity embedding.
"""
from __future__ import annotations

import numpy as np

from defence_studio.services import voice_engine as ve
from defence_studio.voice_lab import audio as A

SR = 16000
HOP_MS = 10
PROSODY_VERSION = "vl-prosody.1"


def _analyzer():
    ve.ensure_path()
    from voice_analyzer import analyzer as an  # EXT-03, same repository
    from voice_analyzer.audio import normalize
    return an, normalize


def semitones(hz, ref_hz):
    return 12.0 * np.log2(np.asarray(hz, dtype=float) / float(ref_hz))


def frames(x16: np.ndarray) -> dict:
    """10 ms frames: f0 (Hz, NaN when unvoiced) and energy (dBFS) for a 16 kHz mono signal."""
    an, normalize = _analyzer()
    norm, _gain = normalize(x16)
    fr = an._frames(norm)
    f0, strength = an._pitch_track(fr)
    db = 20 * np.log10(np.maximum(np.sqrt((an._frames(x16) ** 2).mean(1)), 1e-9))
    db_n = 20 * np.log10(np.maximum(np.sqrt((fr ** 2).mean(1)), 1e-9))
    voiced = (strength > an.VOICED_ACF) & (db_n > db_n.max() - 30)
    f0 = np.where(voiced, f0, np.nan)
    return {"hop_ms": HOP_MS, "f0_hz": f0, "energy_db": db}


def _syllable_estimate(env_db: np.ndarray) -> int:
    """Count energy-envelope peaks (>=100 ms apart, >=3 dB prominence). An ESTIMATE, not a syllable parse."""
    if env_db.size < 3:
        return 0
    k = 5
    sm = np.convolve(env_db, np.ones(k) / k, mode="same")
    peaks, last = 0, -999
    for i in range(1, len(sm) - 1):
        if sm[i] >= sm[i - 1] and sm[i] > sm[i + 1] and i - last >= 10:
            lo = min(sm[max(0, i - 10):i + 1].min(), sm[i:i + 11].min())
            if sm[i] - lo >= 3.0:
                peaks += 1
                last = i
    return peaks


def analyse_signal(x16: np.ndarray, *, min_pause_ms: int = 250) -> dict:
    """Measure delivery from a 16 kHz mono signal. Returns frames + phrases + summary + quality."""
    fr = frames(x16)
    f0, db = fr["f0_hz"], fr["energy_db"]
    total_ms = int(len(x16) * 1000 / SR)
    regions = A.speech_regions(x16, SR, min_silence_ms=min_pause_ms, min_speech_ms=150, hop_ms=HOP_MS)
    voiced_all = f0[~np.isnan(f0)]
    if voiced_all.size < 10 or not regions:
        return {"frames": fr, "phrases": [], "summary": None, "total_ms": total_ms,
                "quality": {"status": "NO_SPEECH", "voiced_frames": int(voiced_all.size)}}
    g_med = float(np.median(voiced_all))
    speech_mask = np.zeros(len(db), bool)
    for s, e in regions:
        speech_mask[s // HOP_MS:e // HOP_MS] = True
    g_energy = float(np.median(db[speech_mask])) if speech_mask.any() else float(np.median(db))
    e_p90 = float(np.percentile(db[speech_mask], 90)) if speech_mask.any() else g_energy

    phrases = []
    for i, (s, e) in enumerate(regions):
        a, b = s // HOP_MS, max(s // HOP_MS + 1, e // HOP_MS)
        pf = f0[a:b]
        pv = pf[~np.isnan(pf)]
        pe = db[a:b]
        nxt = regions[i + 1][0] if i + 1 < len(regions) else None
        syl = _syllable_estimate(pe)
        dur_s = (e - s) / 1000
        item = {
            "ordinal": i, "t0_ms": int(s), "t1_ms": int(e), "duration_ms": int(e - s),
            "pause_before_ms": int(s - (regions[i - 1][1] if i else 0)),
            "pause_after_ms": int(nxt - e) if nxt is not None else int(total_ms - e),
            "voiced_ratio": round(float(pv.size / max(1, pf.size)), 3),
            "energy_rel_db": round(float(np.mean(pe) - g_energy), 2),
            "syllables_estimate": syl,
            "rate_syl_per_s_estimate": round(syl / dur_s, 2) if dur_s > 0 else None,
        }
        if pv.size >= 5:
            st = semitones(pv, g_med)
            third = max(1, len(st) // 3)
            item.update({
                "f0_median_hz": round(float(np.median(pv)), 1),
                "f0_min_hz": round(float(np.percentile(pv, 5)), 1),
                "f0_max_hz": round(float(np.percentile(pv, 95)), 1),
                "rel_median_st": round(float(np.median(st)), 2),
                "range_st": round(float(np.percentile(st, 95) - np.percentile(st, 5)), 2),
                "slope_st": round(float(np.median(st[-third:]) - np.median(st[:third])), 2),
                "intonation": ("FALLING" if np.median(st[-third:]) - np.median(st[:third]) < -1.0 else
                               "RISING" if np.median(st[-third:]) - np.median(st[:third]) > 1.0 else "LEVEL"),
            })
        else:
            item.update({"f0_median_hz": None, "f0_min_hz": None, "f0_max_hz": None, "rel_median_st": None,
                         "range_st": None, "slope_st": None, "intonation": None})
        # Emphasis: frames well above the speech-energy p90 and above the phrase pitch median.
        emph = []
        if pv.size >= 5:
            pmed = np.nanmedian(pf)
            hot = (pe > e_p90) & (np.nan_to_num(pf, nan=0) > pmed * 2 ** (2 / 12))
            j = 0
            while j < len(hot):
                if hot[j]:
                    k = j
                    while k < len(hot) and hot[k]:
                        k += 1
                    if k - j >= 3:
                        emph.append({"t_ms": int(s + j * HOP_MS), "duration_ms": int((k - j) * HOP_MS),
                                     "strength": round(float(min(1.0, (pe[j:k].max() - e_p90) / 6 + 0.3)), 2)})
                    j = k
                else:
                    j += 1
        item["emphasis"] = emph
        phrases.append(item)

    speech_ms = sum(p["duration_ms"] for p in phrases)
    syl_total = sum(p["syllables_estimate"] for p in phrases)
    pauses = [p["pause_after_ms"] for p in phrases[:-1]]
    st_all = semitones(voiced_all, g_med)
    summary = {
        "total_ms": total_ms, "speech_ms": speech_ms, "phrase_count": len(phrases),
        "leading_silence_ms": int(regions[0][0]), "trailing_silence_ms": int(total_ms - regions[-1][1]),
        "f0_median_hz": round(g_med, 1), "f0_min_hz": round(float(np.percentile(voiced_all, 5)), 1),
        "f0_max_hz": round(float(np.percentile(voiced_all, 95)), 1),
        "f0_variation_st": round(float(np.std(st_all)), 2),
        "pitch_range_st": round(float(np.percentile(st_all, 95) - np.percentile(st_all, 5)), 2),
        "rate_syl_per_s_estimate": round(syl_total / (speech_ms / 1000), 2) if speech_ms else None,
        "articulation_note": "syllable counts are energy-peak ESTIMATES",
        "pause_count": len(pauses), "pause_mean_ms": round(float(np.mean(pauses)), 1) if pauses else None,
        "pause_max_ms": max(pauses) if pauses else None,
        "energy_speech_median_db": round(g_energy, 2),
        "energy_dynamic_range_db": round(float(np.percentile(db[speech_mask], 95) - np.percentile(db[speech_mask], 10)), 2),
        "emphasis_count": sum(len(p["emphasis"]) for p in phrases),
    }
    voiced_ratio = float((~np.isnan(f0[speech_mask])).mean()) if speech_mask.any() else 0.0
    q = {"status": "OK" if voiced_ratio > 0.3 and len(phrases) >= 1 else "LOW_CONFIDENCE",
         "voiced_ratio_in_speech": round(voiced_ratio, 3), "frames": int(len(db)),
         "pitch_method": "EXT-03 autocorrelation tracker, 60-400 Hz, 10 ms hop",
         "confidence": round(min(1.0, voiced_ratio * 1.5) * (1.0 if speech_ms > 1500 else 0.6), 3)}
    return {"frames": fr, "phrases": phrases, "summary": summary, "total_ms": total_ms, "quality": q}


def classify_regions(x16: np.ndarray, phrases: list[dict], total_ms: int) -> list[dict]:
    """Speech / silence / non-speech-sound regions. Music/effects are *flagged*, not separated."""
    out, cursor = [], 0
    db = A.frame_db(x16, SR)
    floor = float(np.percentile(db, 10)) if db.size else -120

    def gap(a, b):
        if b - a < 20:
            return
        seg = db[a // HOP_MS:b // HOP_MS]
        loud = seg.size and float(np.median(seg)) > floor + 12
        out.append({"t0_ms": a, "t1_ms": b, "classification": "NON_SPEECH_SOUND" if loud else "SILENCE",
                    "confidence": 0.5 if loud else 0.8})

    for p in phrases:
        gap(cursor, p["t0_ms"])
        out.append({"t0_ms": p["t0_ms"], "t1_ms": p["t1_ms"], "classification": "SPEECH",
                    "confidence": round(min(1.0, 0.4 + p["voiced_ratio"]), 2)})
        cursor = p["t1_ms"]
    gap(cursor, total_ms)
    return out


def emotion_estimate(summary: dict | None) -> dict:
    """Heuristic delivery-intensity estimate from measured numbers. Always an INFERENCE for human review."""
    if not summary:
        return {"label": None, "intensity": None, "confidence": 0.0, "kind": "INFERENCE"}
    var, rng, dyn = summary["f0_variation_st"], summary["pitch_range_st"], summary["energy_dynamic_range_db"]
    rate = summary["rate_syl_per_s_estimate"] or 0
    intensity = float(np.clip(0.35 * (var / 4) + 0.25 * (rng / 12) + 0.2 * (dyn / 25) + 0.2 * (rate / 7), 0, 1))
    label = "CALM" if intensity < 0.35 else ("ENGAGED" if intensity < 0.6 else "INTENSE")
    return {"label": label, "intensity": round(intensity, 2), "confidence": 0.35, "kind": "INFERENCE",
            "method": "weighted pitch variation / range / energy range / rate; review required"}
