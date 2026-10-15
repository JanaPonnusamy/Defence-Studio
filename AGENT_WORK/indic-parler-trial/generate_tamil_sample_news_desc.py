"""Second isolated Indic Parler-TTS run. Does not overwrite the first WAV."""
from __future__ import annotations

import json
import os
import time
import wave
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf
import torch
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer

REPO = Path(r"E:\Jana Soft\DefenceStudio")
OUT_DIR = REPO / "AGENT_WORK" / "indic-parler-trial" / "output"
FIRST_WAV = OUT_DIR / "tamil_female_sample.wav"
WAV = OUT_DIR / "tamil_female_sample_news_desc.wav"
REPORT = REPO / "AGENT_WORK" / "indic-parler-trial" / "trial_report.json"
MODEL_ID = "ai4bharat/indic-parler-tts"

PROMPT = (
    "வணக்கம்! இது Defence Era செய்தி அறிக்கை. "
    "இந்தியாவின் பாதுகாப்புத் துறையில் இன்று முக்கியமான முன்னேற்றங்கள் வெளியாகியுள்ளன. "
    "உறுதிப்படுத்தப்பட்ட தகவல்களைத் தெளிவாகப் பார்ப்போம்."
)
DESCRIPTION = (
    "A female Tamil news narrator speaks with a confident, authoritative, engaging delivery. "
    "Her pace is moderately fast but clear, with deliberate pauses between important statements, "
    "precise diction, controlled pitch variation, strong emphasis on key facts, and a polished "
    "broadcast-news rhythm. The voice is energetic but not exaggerated. The recording is very "
    "clear, close, and free of background noise."
)

CONTROL_NOTE = (
    "Parler controls gender, pace, pitch, clarity, and noise via the caption. "
    "Word-level emphasis and pauses are requested in the caption but are not independently "
    "guaranteed by extra API parameters; none were invented."
)


def _redact(text: str) -> str:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = os.environ.get(key) or ""
        if val:
            text = text.replace(val, "[REDACTED]")
    return text


def _load_pretrained(cls, model_id: str, *, device: str | None = None):
    try:
        obj = cls.from_pretrained(model_id, local_files_only=True)
    except Exception as first:
        msg = _redact(str(first))
        if "401" in msg or "Unauthorized" in msg:
            raise
        try:
            obj = cls.from_pretrained(model_id)
        except Exception as second:
            raise second from first
    if device is not None:
        obj = obj.to(device)
    return obj


def _merge_report(second: dict) -> dict:
    existing: dict = {}
    if REPORT.exists():
        try:
            existing = json.loads(REPORT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    first_run = existing.get("first_run") or {
        k: existing[k]
        for k in existing
        if k not in ("second_run", "runs")
    }
    merged = {
        "model_id": MODEL_ID,
        "ok": bool(second.get("ok")),
        "tamil_production_status": "BLOCKED",
        "production_voice_engine_modified": False,
        "DEFENCE_ERA_FEMALE_001_registered": False,
        "first_run": first_run,
        "second_run": second,
        "note": (
            "Second run used the user-supplied news caption as the Parler description. "
            "tamil_female_sample.wav was left unchanged. Tamil narration remains BLOCKED "
            "until a Tamil speaker listens. No English substitute was used. No tokens recorded."
        ),
    }
    return merged


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    first_stat = None
    if FIRST_WAV.exists():
        first_stat = (FIRST_WAV.stat().st_size, FIRST_WAV.stat().st_mtime_ns)
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    peak = rss0
    device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.perf_counter()
    report = {
        "model_id": MODEL_ID,
        "device": device,
        "hf_home": os.environ.get("HF_HOME"),
        "venv": str(Path(torch.__file__).resolve().parents[4]),
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "api": "official two-tokenizer Parler (description tokenizer from text_encoder + prompt tokenizer)",
        "prompt": PROMPT,
        "description": DESCRIPTION,
        "control_note": CONTROL_NOTE,
        "local_files_only_preferred": True,
        "HF_TOKEN_set": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")),
        "did_login": False,
        "did_bypass": False,
    }
    try:
        print("PROGRESS: model loading", flush=True)
        model = _load_pretrained(ParlerTTSForConditionalGeneration, MODEL_ID, device=device)
        tokenizer = _load_pretrained(AutoTokenizer, MODEL_ID)
        description_tokenizer = _load_pretrained(
            AutoTokenizer, model.config.text_encoder._name_or_path
        )
        peak = max(peak, proc.memory_info().rss)
        report["load_seconds"] = round(time.perf_counter() - t0, 2)
        report["sampling_rate"] = int(model.config.sampling_rate)
        report["text_encoder"] = model.config.text_encoder._name_or_path
        print(f"PROGRESS: model loaded in {report['load_seconds']}s device={device}", flush=True)

        t1 = time.perf_counter()
        print("PROGRESS: generation start", flush=True)
        description_input_ids = description_tokenizer(DESCRIPTION, return_tensors="pt").to(device)
        prompt_input_ids = tokenizer(PROMPT, return_tensors="pt").to(device)
        with torch.inference_mode():
            generation = model.generate(
                input_ids=description_input_ids.input_ids,
                attention_mask=description_input_ids.attention_mask,
                prompt_input_ids=prompt_input_ids.input_ids,
                prompt_attention_mask=prompt_input_ids.attention_mask,
            )
        peak = max(peak, proc.memory_info().rss)
        report["generate_seconds"] = round(time.perf_counter() - t1, 2)
        print(f"PROGRESS: generation finish in {report['generate_seconds']}s", flush=True)
        audio_arr = generation.cpu().numpy().squeeze().astype(np.float32)
        peak_amp = float(np.max(np.abs(audio_arr))) if audio_arr.size else 0.0
        rms = float(np.sqrt(np.mean(audio_arr.astype(np.float64) ** 2))) if audio_arr.size else 0.0
        sf.write(str(WAV), audio_arr, model.config.sampling_rate)
        with wave.open(str(WAV), "rb") as w:
            nch, sw, sr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        data, _ = sf.read(str(WAV), dtype="float32")
        report["wav"] = {
            "path": str(WAV),
            "bytes": WAV.stat().st_size,
            "channels": nch,
            "sample_width": sw,
            "sample_rate": sr,
            "frames": nf,
            "duration_s": round(nf / sr, 3) if sr else 0,
            "peak_amplitude": round(peak_amp, 5),
            "rms": round(rms, 5),
            "silent": rms < 1e-4,
            "clipped": peak_amp >= 0.99,
            "nonempty": WAV.stat().st_size > 44 and nf > 0,
            "valid_wav": True,
            "exists": WAV.exists(),
            "independent_read_frames": int(np.asarray(data).size),
        }
        report["ok"] = bool(report["wav"]["nonempty"] and not report["wav"]["silent"] and report["wav"]["valid_wav"])
        report["wav_written"] = True
        print(
            "PROGRESS: file verification "
            f"exists={WAV.exists()} bytes={report['wav']['bytes']} "
            f"duration_s={report['wav']['duration_s']} sr={report['wav']['sample_rate']} "
            f"silent={report['wav']['silent']} clipped={report['wav']['clipped']}",
            flush=True,
        )
    except Exception as exc:
        report["ok"] = False
        report["error"] = _redact(f"{type(exc).__name__}: {exc}")
        report["error_type"] = type(exc).__name__
        if "401" in report["error"] or "Unauthorized" in report["error"]:
            report["http_status"] = 401
        elif "403" in report["error"] or "Forbidden" in report["error"]:
            report["http_status"] = 403
        if first_stat is not None:
            cur = (FIRST_WAV.stat().st_size, FIRST_WAV.stat().st_mtime_ns)
            report["first_wav_unchanged"] = cur == first_stat
        merged = _merge_report(report)
        REPORT.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 2
    report["peak_rss_mb"] = round(peak / (1024 * 1024), 1)
    report["delta_rss_mb"] = round((peak - rss0) / (1024 * 1024), 1)
    report["total_seconds"] = round(time.perf_counter() - t0, 2)
    if first_stat is not None:
        cur = (FIRST_WAV.stat().st_size, FIRST_WAV.stat().st_mtime_ns)
        report["first_wav_unchanged"] = cur == first_stat
        report["first_wav_bytes"] = first_stat[0]
    merged = _merge_report(report)
    REPORT.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("ok", "model_id", "wav", "generate_seconds", "peak_rss_mb", "first_wav_unchanged") if k in report}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
