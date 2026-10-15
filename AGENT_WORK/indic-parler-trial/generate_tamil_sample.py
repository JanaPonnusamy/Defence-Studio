"""Isolated Indic Parler-TTS trial. Cache and output stay off production paths."""
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
WAV = OUT_DIR / "tamil_female_sample.wav"
REPORT = REPO / "AGENT_WORK" / "indic-parler-trial" / "trial_report.json"
MODEL_ID = "ai4bharat/indic-parler-tts"

PROMPT = (
    "வணக்கம்! இது Defence Era செய்தி அறிக்கை. "
    "இந்தியாவின் பாதுகாப்புத் துறையில் இன்று முக்கியமான முன்னேற்றங்கள் வெளியாகியுள்ளன. "
    "உறுதிப்படுத்தப்பட்ட தகவல்களைத் தெளிவாகப் பார்ப்போம்."
)
# Official Tamil recommended speaker is Jaya; caption follows the model-card speaker style.
DESCRIPTION = (
    "Jaya speaks at a moderate pace with a clear female newsreader delivery. "
    "Her voice is natural Tamil, slightly expressive, close-sounding, with very clear audio "
    "and no background noise."
)


def _redact(text: str) -> str:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = os.environ.get(key) or ""
        if val:
            text = text.replace(val, "[REDACTED]")
    return text


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
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
        "prompt": PROMPT,
        "description": DESCRIPTION,
    }
    try:
        model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL_ID).to(device)
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        description_tokenizer = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
        peak = max(peak, proc.memory_info().rss)
        report["load_seconds"] = round(time.perf_counter() - t0, 2)
        report["sampling_rate"] = int(model.config.sampling_rate)
        report["text_encoder"] = model.config.text_encoder._name_or_path

        t1 = time.perf_counter()
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
        audio_arr = generation.cpu().numpy().squeeze().astype(np.float32)
        peak_amp = float(np.max(np.abs(audio_arr))) if audio_arr.size else 0.0
        rms = float(np.sqrt(np.mean(audio_arr.astype(np.float64) ** 2))) if audio_arr.size else 0.0
        if WAV.exists():
            WAV.unlink()
        sf.write(str(WAV), audio_arr, model.config.sampling_rate)
        with wave.open(str(WAV), "rb") as w:
            nch, sw, sr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
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
        }
        report["ok"] = bool(report["wav"]["nonempty"] and not report["wav"]["silent"])
    except Exception as exc:
        report["ok"] = False
        report["error"] = _redact(f"{type(exc).__name__}: {exc}")
        report["error_type"] = type(exc).__name__
        if "401" in report["error"] or "Unauthorized" in report["error"]:
            report["http_status"] = 401
        elif "403" in report["error"] or "Forbidden" in report["error"]:
            report["http_status"] = 403
        REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 2
    report["peak_rss_mb"] = round(peak / (1024 * 1024), 1)
    report["delta_rss_mb"] = round((peak - rss0) / (1024 * 1024), 1)
    report["total_seconds"] = round(time.perf_counter() - t0, 2)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("ok", "model_id", "wav", "generate_seconds", "peak_rss_mb") if k in report}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
