"""Measured delivery analysis -> Voice Style Profile content. Not identity cloning."""
from __future__ import annotations
import hashlib, json
import numpy as np
from .audio import SR, NoSpeechError, dbfs, load_audio, normalize

ANALYSIS_VERSION = "1.0"
FRAME, HOP = int(0.040 * SR), int(0.010 * SR)
HOP_MS = 10
F0_MIN, F0_MAX = 60.0, 400.0
VOICED_ACF = 0.45
MIN_SPEECH_MS, MERGE_GAP_MS, MIN_PAUSE_MS = 150, 120, 120
PAUSE_BANDS = [(250, "SHORT"), (500, "NORMAL"), (650, "LONG")]  # >= last => DRAMATIC
TEMPO_SLOW, TEMPO_FAST = 0.85, 1.15
ABS_FLOOR_DB = -55.0


def _frames(x):
    if len(x) < FRAME:
        x = np.pad(x, (0, FRAME - len(x)))
    n = 1 + (len(x) - FRAME) // HOP
    return x[np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]]


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def _pitch_track(fr):
    w = fr * np.hanning(FRAME)[None, :]
    spec = np.fft.rfft(w, 2 * FRAME, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :FRAME]
    ac = ac / np.maximum(ac[:, :1], 1e-12)
    lo, hi = int(SR / F0_MAX), int(SR / F0_MIN)
    seg = ac[:, lo:hi]
    lag = seg.argmax(1) + lo
    return SR / lag, seg.max(1)


def _segments(db):
    """Speech/silence by adaptive energy threshold. Returns [(start_frame,end_frame)]."""
    p10, p95 = np.percentile(db, 10), np.percentile(db, 95)
    if p95 < ABS_FLOOR_DB or (p95 - p10) < 6.0:
        return []
    thr = max(ABS_FLOOR_DB, p10 + 0.35 * (p95 - p10))
    act = db > thr
    runs, i, n = [], 0, len(act)
    while i < n:
        if act[i]:
            j = i
            while j < n and act[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    merged = []
    for s, e in runs:
        if merged and (s - merged[-1][1]) * HOP_MS < MERGE_GAP_MS:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if (e - s) * HOP_MS >= MIN_SPEECH_MS]


def _syllables(env_db, s, e):
    seg = env_db[s:e]
    if len(seg) < 3:
        return 0
    sm = np.convolve(seg, np.ones(5) / 5, mode="same")
    thr = np.percentile(sm, 50)
    peaks, last = 0, -999
    for i in range(1, len(sm) - 1):
        if sm[i] > sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] > thr and i - last >= 8:
            peaks += 1
            last = i
    return peaks


def _pause_type(ms):
    for lim, name in PAUSE_BANDS:
        if ms < lim:
            return name
    return "DRAMATIC"


def _contour(t_ms, hz, step=100, cap=600):
    out, last = [], -step
    for t, f in zip(t_ms, hz):
        if t - last >= step:
            out.append([int(t), _r(f, 1)])
            last = t
    return out[:cap]


def _pstats(f):
    if len(f) < 5:
        return None
    st = 12 * np.log2(f / np.median(f))
    return {"mean_hz": _r(f.mean(), 2), "median_hz": _r(np.median(f), 2), "min_hz": _r(f.min(), 2),
            "max_hz": _r(f.max(), 2), "range_hz": _r(f.max() - f.min(), 2),
            "variation_semitones_std": _r(st.std(), 3),
            "stability": _r(1 - min(st.std() / 6.0, 1.0), 3)}


def _counts(xs):
    out = {}
    for x in xs:
        if x:
            out[x] = out.get(x, 0) + 1
    return out


def _trend(rates, sign):
    if len(rates) < 4:
        return None
    h = len(rates) // 2
    base = float(np.mean(rates[:h])) or 1.0
    d = (float(np.mean(rates[h:])) - base) / base
    return _r(d, 3) if d * sign > 0.1 else 0.0


def _emotion(sg):
    t = sg["tempo"]["tempo"] or 1.0
    p = (sg["pitch"] or {}).get("variation_semitones_std") or 0.0
    e = sg["emphasis"]["score"] if sg["emphasis"] else 0.0
    s = {"CALM": 1 - min(abs(t - 0.9) + p / 8, 1), "SERIOUS": 1 - min(abs(t - 0.95) + abs(p - 2) / 6, 1),
         "URGENT": min(max(t - 1.0, 0) * 3 + e * 0.4, 1), "DRAMATIC": min(e * 0.7 + p / 12, 1),
         "NEUTRAL": 0.3}
    lab = max(s, key=lambda k: (s[k], k))
    return {"label": lab, "score": _r(s[lab], 3), "confidence": _r(min(sg["confidence"], 0.6), 3),
            "kind": "INFERENCE", "analysis_version": ANALYSIS_VERSION}


def analyze_audio(path: str, *, language: str, transcript: str | None = None,
                  max_seconds: float = 3600.0) -> dict:
    """Return {"profile": ..., "segments": [...]} content (no ids). Deterministic for identical input."""
    orig = load_audio(path, max_seconds)
    norm, gain = normalize(orig)
    total_ms = int(len(orig) / SR * 1000)
    fr_n, fr_o = _frames(norm), _frames(orig)
    db_n = 20 * np.log10(np.maximum(np.sqrt((fr_n ** 2).mean(1)), 1e-9))
    db_o = 20 * np.log10(np.maximum(np.sqrt((fr_o ** 2).mean(1)), 1e-9))
    f0, strength = _pitch_track(fr_n)
    segs = _segments(db_n)
    if not segs:
        raise NoSpeechError("no speech detected")
    voiced_all = strength > VOICED_ACF
    t_ms = np.arange(len(db_n)) * HOP_MS

    rows = []
    for s, e in segs:
        v = voiced_all[s:e] & (db_n[s:e] > db_n[s:e].max() - 30)
        syl = _syllables(db_n, s, e)
        dur = (e - s) * HOP_MS / 1000
        rows.append(dict(s=s, e=e, f=f0[s:e][v], tms=t_ms[s:e][v], syl=syl, dur=dur,
                         rate=syl / dur if dur > 0 else 0.0, rms_db=float(db_o[s:e].mean()),
                         peak=dbfs(float(np.abs(orig[s * HOP:e * HOP + FRAME]).max())),
                         voiced=float(v.mean())))
    rates = np.array([r["rate"] for r in rows if r["rate"] > 0])
    base_rate = float(np.median(rates)) if rates.size else 0.0
    all_f = np.concatenate([r["f"] for r in rows])
    all_t = np.concatenate([r["tms"] for r in rows])
    snr = float(np.percentile(db_n, 95) - np.percentile(db_n, 10))
    speech_ms = sum((e - s) * HOP_MS for s, e in segs)

    segments = []
    for k, r in enumerate(rows):
        start, end = r["s"] * HOP_MS, r["e"] * HOP_MS
        prev_end = rows[k - 1]["e"] * HOP_MS if k else 0
        next_start = rows[k + 1]["s"] * HOP_MS if k + 1 < len(rows) else total_ms
        tempo = (r["rate"] / base_rate) if base_rate else None
        tclass = None if tempo is None else ("SLOW" if tempo < TEMPO_SLOW else "FAST" if tempo > TEMPO_FAST else "NORMAL")
        ps = _pstats(r["f"])
        inton = {"class": None, "end_slope_semitones": None, "method": "median(last40%)-median(first40%) of voiced f0"}
        if ps and len(r["f"]) >= 10:
            h = max(2, int(len(r["f"]) * 0.4))
            d = 12 * np.log2(np.median(r["f"][-h:]) / np.median(r["f"][:h]))
            inton["end_slope_semitones"] = _r(d, 3)
            inton["class"] = "RISING" if d > 1.0 else "FALLING" if d < -1.0 else "NEUTRAL"
            if ps["variation_semitones_std"] > 4.0 and abs(d) < 1.0:
                inton["class"] = "MIXED"
        conf = _r(min(1.0, 0.5 * r["voiced"] + 0.3 * min(r["dur"], 1.0) + 0.2 * min(snr / 30, 1.0)), 3)
        segments.append({
            "segment_id": f"seg-{k:04d}", "start_ms": start, "end_ms": end,
            "pitch": ps if ps else {"value": None, "reason": "LOW_VOICING_CONFIDENCE"},
            "speaking_rate": {"syllables_per_sec": _r(r["rate"], 3), "syllables": r["syl"],
                              "kind": "ESTIMATED", "method": "energy_envelope_peaks"},
            "tempo": {"tempo": _r(tempo, 3), "tempo_class": tclass, "confidence": conf if tempo else 0.0},
            "energy": {"mean_dbfs": _r(r["rms_db"], 2), "peak_dbfs": _r(r["peak"], 2)},
            "pause_before_ms": int(start - prev_end), "pause_after_ms": int(next_start - end),
            "intonation": inton, "emphasis": None, "emotion": None, "confidence": conf})

    # emphasis candidates: jointly elevated pitch/energy/duration relative to the speaker's own baseline
    means = [r["f"].mean() for r in rows if len(r["f"])]
    med_f = float(np.median(means)) if means else 0.0
    en = np.array([r["rms_db"] for r in rows])
    en_sd = en.std() or 1.0
    durs = np.array([r["dur"] for r in rows])
    d_sd = durs.std() or 1.0
    for sg, r in zip(segments, rows):
        if len(r["f"]) and med_f:
            pz = 12 * np.log2(r["f"].mean() / med_f) / 2.0
            ez = (r["rms_db"] - en.mean()) / en_sd
            dz = (r["dur"] - durs.mean()) / d_sd
            score = float(1 / (1 + np.exp(-(0.9 * pz + 0.7 * ez + 0.3 * dz - 0.5))))
            sg["emphasis"] = {"score": _r(score, 3), "candidate": bool(score >= 0.6), "text": None,
                              "signals": {"pitch_z": _r(pz, 2), "energy_z": _r(ez, 2), "duration_z": _r(dz, 2)}}
        sg["emotion"] = _emotion(sg)

    pauses = []
    for k in range(len(rows) - 1):
        ms = (rows[k + 1]["s"] - rows[k]["e"]) * HOP_MS
        if ms >= MIN_PAUSE_MS:
            pauses.append({"after_segment": f"seg-{k:04d}", "start_ms": rows[k]["e"] * HOP_MS,
                           "pause_type": _pause_type(ms), "duration_ms": int(ms),
                           "context": "after_sentence_candidate", "confidence": 0.9})
    pms = np.array([p["duration_ms"] for p in pauses])
    segdb = db_o[np.concatenate([np.arange(s, e) for s, e in segs])]
    means_db = [sg["energy"]["mean_dbfs"] for sg in segments]
    trans = []
    for k in range(len(means_db) - 1):
        d = means_db[k + 1] - means_db[k]
        if abs(d) >= 3:
            trans.append({"at_ms": segments[k]["end_ms"], "delta_db": _r(d, 2),
                          "type": "SOFT_TO_STRONG" if d > 0 else "STRONG_TO_SOFT"})
    words = len(transcript.split()) if transcript and transcript.strip() else None
    cls = [sg["tempo"]["tempo_class"] for sg in segments]
    emo = _counts([sg["emotion"]["label"] for sg in segments])
    n_emo = sum(emo.values()) or 1
    profile = {
        "language": language, "analysis_version": ANALYSIS_VERSION,
        "audio": {"duration_ms": total_ms, "speech_ms": int(speech_ms), "sample_rate": SR,
                  "normalization_gain_db": _r(gain, 2), "segment_count": len(segs)},
        "pitch": {**(_pstats(all_f) or {"value": None, "reason": "LOW_VOICING_CONFIDENCE"}),
                  "unit": "Hz", "method": "autocorrelation_40ms_frame_10ms_hop",
                  "voiced_ratio": _r(np.mean([r["voiced"] for r in rows]), 3),
                  "contour_hz": _contour(all_t, all_f)},
        "speaking_rate": {
            "words_per_minute": _r(words / (speech_ms / 60000), 1) if words else None,
            "words_per_minute_kind": "MEASURED_FROM_TRANSCRIPT" if words else "UNAVAILABLE",
            "syllables_per_sec": _r(sum(r["syl"] for r in rows) / (speech_ms / 1000), 3),
            "syllables_kind": "ESTIMATED", "normal_rate_syllables_per_sec": _r(base_rate, 3),
            "fast_segments": [s["segment_id"] for s, c in zip(segments, cls) if c == "FAST"],
            "slow_segments": [s["segment_id"] for s, c in zip(segments, cls) if c == "SLOW"]},
        "tempo": {"normal_rate_syllables_per_sec": _r(base_rate, 3), "class_counts": _counts(cls),
                  "acceleration": _trend(rates, +1), "deceleration": _trend(rates, -1),
                  "segments": [{"segment_id": s["segment_id"], **s["tempo"]} for s in segments]},
        "pause_profile": {"pause_before_speech_ms": int(rows[0]["s"] * HOP_MS), "count": len(pauses),
                          "type_counts": _counts([p["pause_type"] for p in pauses]),
                          "mean_ms": _r(pms.mean(), 1) if pms.size else None,
                          "max_ms": int(pms.max()) if pms.size else None, "pauses": pauses},
        "energy_profile": {"unit": "dBFS", "mean_dbfs": _r(10 * np.log10(np.mean(10 ** (segdb / 10))), 2),
                           "rms_dbfs": _r(dbfs(float(np.sqrt(np.mean(orig ** 2)))), 2),
                           "peak_dbfs": _r(dbfs(float(np.abs(orig).max())), 2),
                           "dynamic_range_db": _r(np.percentile(segdb, 95) - np.percentile(segdb, 10), 2),
                           "variation_db_std": _r(segdb.std(), 2), "transitions": trans,
                           "note": "loudness is not emotion"},
        "intonation_profile": {"sentence_ending_counts": _counts([s["intonation"]["class"] for s in segments]),
                               "method": "per-segment end slope in semitones"},
        "rhythm_profile": {"speech_density": _r(speech_ms / total_ms, 3),
                           "pause_to_speech_ratio": _r((total_ms - speech_ms) / speech_ms, 3),
                           "phrase_duration_ms_mean": _r(speech_ms / len(segs), 1),
                           "phrase_duration_ms": [s["end_ms"] - s["start_ms"] for s in segments]},
        "emphasis_profile": {"candidates": [{"segment_id": s["segment_id"], "start_ms": s["start_ms"],
                                             "end_ms": s["end_ms"], "text": None,
                                             "emphasis_score": s["emphasis"]["score"]}
                                            for s in segments if s["emphasis"] and s["emphasis"]["candidate"]],
                             "word_alignment": "UNAVAILABLE_NO_TRANSCRIPT_ALIGNMENT"},
        "emotion_profile": {"kind": "INFERENCE", "analysis_version": ANALYSIS_VERSION,
                            "label_share": {k: _r(v / n_emo, 3) for k, v in sorted(emo.items())}},
        "confidence": {"overall": _r(float(np.mean([s["confidence"] for s in segments])), 3),
                       "snr_proxy_db": _r(snr, 1),
                       "warnings": (["SHORT_AUDIO"] if speech_ms < 3000 else []) +
                                   (["LOW_DYNAMIC_RANGE_NOISY"] if snr < 15 else [])},
        "source_segments": [s["segment_id"] for s in segments],
    }
    return {"profile": profile, "segments": segments}


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def analysis_hash(content: dict) -> str:
    return hashlib.sha256(canonical(content).encode()).hexdigest()
