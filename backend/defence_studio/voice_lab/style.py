"""Cross-voice delivery matching (VL-6).

Input: the narrator's OWN generated audio, plus a stored style profile measured from a
reference recording. Output: the same narrator audio with the reference's *delivery*
applied, at phrase level:

* timing: each phrase is time-stretched toward the reference phrase duration, capped
  (default 0.85-1.15). Pauses are set to the reference pause durations.
* relative pitch: each phrase's median is moved so that its offset from the narrator's
  OWN median (in semitones) follows the reference phrase's offset from the reference
  speaker's median, scaled and clamped to the narrator's natural range. Absolute
  reference Hz values are never used as targets.
* energy: per-phrase level offsets follow the reference, clamped to +/-6 dB.

Pitch/tempo use ffmpeg's rubberband filter with ``formant=preserved``, so the
narrator's spectral envelope (timbre, i.e. identity) is kept. No speaker embedding is
computed or transferred. Every applied value and every cap hit is reported.
"""
from __future__ import annotations

import numpy as np

from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab import prosody as P

STYLE_VERSION = "vl-style-transfer.1"
DEFAULTS = {"min_stretch": 0.85, "max_stretch": 1.15, "pitch_scale": 1.0, "max_pitch_shift_st": 4.0,
            "max_gain_db": 6.0, "max_pause_ms": 3000, "edge_fade_ms": 8}
DEFAULT_RANGE_ST = 6.0


def profile_features(analysis: dict) -> dict:
    """Reusable, identity-free style features from a reference analysis."""
    s = analysis["summary"]
    return {
        "style_version": STYLE_VERSION,
        "total_ms": analysis["total_ms"],
        "leading_silence_ms": s["leading_silence_ms"],
        "trailing_silence_ms": s["trailing_silence_ms"],
        "phrases": [{k: p[k] for k in ("duration_ms", "pause_after_ms", "rel_median_st", "range_st", "slope_st",
                                       "energy_rel_db", "intonation", "rate_syl_per_s_estimate")}
                    | {"emphasis_rel_ms": [e["t_ms"] - p["t0_ms"] for e in p["emphasis"]]}
                    for p in analysis["phrases"]],
        "global": {"pitch_variation_st": s["f0_variation_st"], "pitch_range_st": s["pitch_range_st"],
                   "rate_syl_per_s_estimate": s["rate_syl_per_s_estimate"], "pause_mean_ms": s["pause_mean_ms"],
                   "energy_dynamic_range_db": s["energy_dynamic_range_db"]},
        # Absolute reference pitch is kept ONLY as provenance; transfer never targets it.
        "provenance_only": {"reference_f0_median_hz": s["f0_median_hz"]},
    }


def _group_regions(regions: list[tuple[int, int]], n: int) -> list[tuple[int, int]] | None:
    """Merge generated speech regions into n phrases by cutting at the n-1 longest gaps."""
    if len(regions) < n:
        return None
    if len(regions) == n:
        return list(regions)
    gaps = sorted(range(len(regions) - 1), key=lambda i: regions[i + 1][0] - regions[i][1], reverse=True)[:n - 1]
    cuts = sorted(gaps)
    out, start = [], 0
    for c in cuts + [len(regions) - 1]:
        out.append((regions[start][0], regions[c][1]))
        start = c + 1
    return out


def _fade(x: np.ndarray, sr: int, ms: int) -> np.ndarray:
    n = min(len(x) // 2, sr * ms // 1000)
    if n > 0:
        r = np.linspace(0, 1, n, dtype=np.float32)
        x = x.copy()
        x[:n] *= r
        x[-n:] *= r[::-1]
    return x


def _process(seg: np.ndarray, sr: int, duration_ratio: float, pitch_st: float) -> np.ndarray:
    if abs(duration_ratio - 1) < 1e-3 and abs(pitch_st) < 0.05:
        return seg
    tempo = 1.0 / duration_ratio
    ratio = 2 ** (pitch_st / 12)
    return A.run_filter(seg, sr, f"rubberband=tempo={tempo:.5f}:pitch={ratio:.5f}:formant=preserved:transients=smooth")


def narrator_range(analysis: dict, configured: dict | None) -> dict:
    """The narrator's natural range: configured on the model version if known, else measured on this output."""
    s = analysis["summary"] or {}
    cfg = configured or {}
    if cfg.get("f0_median_hz"):
        med = cfg["f0_median_hz"]
        return {"median_hz": med, "min_hz": cfg.get("f0_min_hz") or med * 2 ** (-DEFAULT_RANGE_ST / 12),
                "max_hz": cfg.get("f0_max_hz") or med * 2 ** (DEFAULT_RANGE_ST / 12), "source": "model_version_config"}
    # The spread of ONE (often flat) generation is not the narrator's natural range; anchor on her
    # measured median and allow +/- DEFAULT_RANGE_ST semitones around it. Never the reference's pitch.
    med = s.get("f0_median_hz")
    return {"median_hz": med, "min_hz": med * 2 ** (-DEFAULT_RANGE_ST / 12) if med else None,
            "max_hz": med * 2 ** (DEFAULT_RANGE_ST / 12) if med else None,
            "source": f"measured median of this generation +/-{DEFAULT_RANGE_ST:g} st (configure f0 range on the "
                      "model version for a measured natural range)"}


def apply_style(x: np.ndarray, sr: int, features: dict | None, *, target_duration_ms: int | None = None,
                narrator_cfg: dict | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
    """Return (styled audio, report). ``features`` None means timing-only (target duration)."""
    opt = {**DEFAULTS, **(options or {})}
    x = np.asarray(x, np.float32)
    x16 = _resample(x, sr, 16000) if sr != 16000 else x
    raw = P.analyse_signal(x16)
    raw_ms = int(len(x) * 1000 / sr)
    rng = narrator_range(raw, narrator_cfg)
    report = {"style_version": STYLE_VERSION, "options": opt, "raw_duration_ms": raw_ms, "narrator_range": rng,
              "identity": "narrator's own generated audio; formant-preserving pitch/tempo; no reference embedding"}

    if features is None:
        if not target_duration_ms:
            report.update(mode="NONE", output_duration_ms=raw_ms)
            return x, report
        want = target_duration_ms / raw_ms
        got = float(np.clip(want, opt["min_stretch"], opt["max_stretch"]))
        y = _process(x, sr, got, 0.0)
        out_ms = int(len(y) * 1000 / sr)
        report.update(mode="TIMING_ONLY", target_duration_ms=target_duration_ms, stretch_requested=round(want, 4),
                      stretch_applied=round(got, 4), capped=abs(want - got) > 1e-6, output_duration_ms=out_ms,
                      duration_diff_ms=out_ms - target_duration_ms)
        return y, report

    ref_phr = features["phrases"]
    ref_total = features["total_ms"]
    scale = (target_duration_ms / ref_total) if target_duration_ms else 1.0
    regions = A.speech_regions(x16, 16000, min_silence_ms=120, min_speech_ms=80)
    groups = _group_regions(regions, len(ref_phr)) if ref_phr else None
    report.update(reference_duration_ms=ref_total, target_duration_ms=int(round(ref_total * scale)),
                  time_scale_vs_reference=round(scale, 4), reference_phrases=len(ref_phr),
                  generated_regions=len(regions))
    if not groups:
        # Not enough generated phrases to map one-to-one: global timing only, reported honestly.
        target = int(round(ref_total * scale))
        y, rep = apply_style(x, sr, None, target_duration_ms=target, narrator_cfg=narrator_cfg, options=options)
        report.update(mode="GLOBAL_FALLBACK", reason=f"generated audio has {len(regions)} speech regions, "
                      f"reference has {len(ref_phr)} phrases", **{k: rep[k] for k in rep if k.startswith(("stretch", "capped",
                                                                                                        "output", "duration"))})
        return y, report

    med = rng["median_hz"]
    lo_hz, hi_hz = rng["min_hz"], rng["max_hz"]
    fade = opt["edge_fade_ms"]
    pieces = [np.zeros(int(sr * min(opt["max_pause_ms"], features["leading_silence_ms"] * scale) / 1000), np.float32)]
    per = []
    for i, ((s_ms, e_ms), ref) in enumerate(zip(groups, ref_phr)):
        seg = x[int(s_ms * sr / 1000):int(e_ms * sr / 1000)]
        gen_dur = max(1, e_ms - s_ms)
        want_ratio = ref["duration_ms"] * scale / gen_dur
        ratio = float(np.clip(want_ratio, opt["min_stretch"], opt["max_stretch"]))
        seg16 = x16[int(s_ms * 16):int(e_ms * 16)]
        pv = P.frames(seg16)["f0_hz"] if len(seg16) > 800 else np.array([np.nan])
        pv = pv[~np.isnan(pv)]
        shift, clamped = 0.0, []
        if ref.get("rel_median_st") is not None and pv.size >= 5 and med:
            gen_rel = float(P.semitones(np.median(pv), med))
            wanted = opt["pitch_scale"] * ref["rel_median_st"] - gen_rel
            shift = float(np.clip(wanted, -opt["max_pitch_shift_st"], opt["max_pitch_shift_st"]))
            if shift != wanted:
                clamped.append("MAX_PITCH_SHIFT")
            new_med = float(np.median(pv)) * 2 ** (shift / 12)
            if lo_hz and new_med < lo_hz:
                shift += float(P.semitones(lo_hz, new_med)); clamped.append("NARRATOR_MIN_HZ")
            if hi_hz and new_med > hi_hz:
                shift -= float(P.semitones(new_med, hi_hz)); clamped.append("NARRATOR_MAX_HZ")
        out = _process(seg, sr, ratio, shift)
        # energy: follow the reference's relative phrase level
        cur = 20 * np.log10(max(float(np.sqrt(np.mean(out ** 2))), 1e-9))
        gain = 0.0
        if ref.get("energy_rel_db") is not None:
            gen_rel_e = cur - float(np.median([20 * np.log10(max(float(np.sqrt(np.mean(
                x[int(a * sr / 1000):int(b * sr / 1000)] ** 2))), 1e-9)) for a, b in groups]))
            gain = float(np.clip(ref["energy_rel_db"] - gen_rel_e, -opt["max_gain_db"], opt["max_gain_db"]))
        out = _fade(out * 10 ** (gain / 20), sr, fade)
        pieces.append(out)
        pause_ms = ref["pause_after_ms"] * scale if i < len(ref_phr) - 1 else features["trailing_silence_ms"] * scale
        pause_ms = min(opt["max_pause_ms"], max(0.0, pause_ms))
        pieces.append(np.zeros(int(sr * pause_ms / 1000), np.float32))
        per.append({"ordinal": i, "reference_ms": int(ref["duration_ms"] * scale), "raw_ms": int(gen_dur),
                    "output_ms": int(len(out) * 1000 / sr), "stretch_requested": round(want_ratio, 4),
                    "stretch_applied": round(ratio, 4), "stretch_capped": abs(want_ratio - ratio) > 1e-6,
                    "pitch_shift_st": round(shift, 2), "pitch_clamps": clamped, "gain_db": round(gain, 2),
                    "pause_after_ms": int(pause_ms)})
    y = np.concatenate(pieces).astype(np.float32)
    peak = float(np.abs(y).max()) if y.size else 0
    if peak > 0.98:
        y *= 0.98 / peak
    out_ms = int(len(y) * 1000 / sr)
    report.update(mode="PHRASE_MAPPED", phrases=per, output_duration_ms=out_ms,
                  duration_diff_ms=out_ms - report["target_duration_ms"],
                  stretch_capped_phrases=sum(p["stretch_capped"] for p in per),
                  pitch_clamped_phrases=sum(bool(p["pitch_clamps"]) for p in per))
    report["comparison"] = compare(features, y, sr, scale)
    return y, report


def _resample(x: np.ndarray, sr: int, target: int) -> np.ndarray:
    n = int(round(len(x) * target / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def compare(features: dict, y: np.ndarray, sr: int, scale: float = 1.0) -> dict:
    """Measured comparison of an output against the reference delivery profile."""
    y16 = _resample(y, sr, 16000) if sr != 16000 else y
    an = P.analyse_signal(y16)
    ref = features["phrases"]
    outp = an["phrases"]
    res = {"output_phrases": len(outp), "reference_phrases": len(ref)}
    # pause / phrase-start alignment on the reference time grid
    ref_starts, t = [], features["leading_silence_ms"] * scale
    for p in ref:
        ref_starts.append(t)
        t += (p["duration_ms"] + p["pause_after_ms"]) * scale
    if len(outp) == len(ref):
        errs = [abs(o["t0_ms"] - r) for o, r in zip(outp, ref_starts)]
        res["phrase_start_error_ms"] = {"mean": round(float(np.mean(errs)), 1), "max": round(float(np.max(errs)), 1)}
        a = [r["rel_median_st"] for r in ref]
        b = [o["rel_median_st"] for o in outp]
        pairs = [(u, v) for u, v in zip(a, b) if u is not None and v is not None]
        if len(pairs) >= 3:
            u, v = np.array(pairs).T
            res["relative_pitch_correlation"] = (round(float(np.corrcoef(u, v)[0, 1]), 3)
                                                 if u.std() > 1e-6 and v.std() > 1e-6 else None)
            res["relative_pitch_mae_st"] = round(float(np.mean(np.abs(u - v))), 2)
    else:
        res["phrase_start_error_ms"] = None
        res["note"] = "phrase counts differ after processing; per-phrase alignment not measurable"
    if an["summary"]:
        res["output_f0_median_hz"] = an["summary"]["f0_median_hz"]
        res["output_pitch_range_st"] = an["summary"]["pitch_range_st"]
    return res
