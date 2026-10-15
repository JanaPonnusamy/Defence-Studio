"""Generate one cached, isolated Tamil Kavitha candidate sample."""
from __future__ import annotations

import hashlib
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
ROOT = REPO / "AGENT_WORK" / "indic-parler-trial"
OUT = ROOT / "output" / "tamil_female_youth_trial.wav"
REPORT = ROOT / "kavitha_trial_report.json"
MODEL_ID = "ai4bharat/indic-parler-tts"
PROMPT = (
    "வணக்கம்! இது Defence Era செய்தி அறிக்கை. "
    "இந்தியாவின் பாதுகாப்புத் துறையில் இன்று முக்கியமான முன்னேற்றங்கள் வெளியாகியுள்ளன. "
    "உறுதிப்படுத்தப்பட்ட தகவல்களைத் தெளிவாகப் பார்ப்போம்."
)
SPEAKER = "Kavitha"
DESCRIPTION = (
    "A young adult female Tamil news presenter with a bright, clear, youthful voice. "
    "Modern Indian Tamil broadcast style, confident and engaging, with a lively medium-fast pace, "
    "crisp Tamil pronunciation, natural conversational intonation, light vocal energy, and precise "
    "emphasis on important facts. Smooth, fresh-sounding delivery with controlled pitch variation "
    "and short natural pauses. Professional contemporary digital news presentation, not a mature radio "
    "announcer, not deep, not slow, not theatrical. Clean studio audio."
)


def main() -> int:
    if OUT.exists():
        raise RuntimeError(f"refusing to overwrite existing trial output: {OUT}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    peak = rss0
    device = "cuda" if torch.cuda.is_available() else "cpu"
    started = time.perf_counter()
    report = {
        "model_id": MODEL_ID,
        "speaker_preset": SPEAKER,
        "device": device,
        "cuda_available": torch.cuda.is_available(),
        "hf_home": os.environ.get("HF_HOME"),
        "prompt": PROMPT,
        "description": DESCRIPTION,
        "local_files_only": True,
        "output": str(OUT),
    }
    model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL_ID, local_files_only=True).to(device)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, local_files_only=True)
    desc_tokenizer = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path, local_files_only=True)
    peak = max(peak, proc.memory_info().rss)
    report["load_seconds"] = round(time.perf_counter() - started, 2)
    report["sampling_rate"] = int(model.config.sampling_rate)
    report["text_encoder"] = model.config.text_encoder._name_or_path
    gen_started = time.perf_counter()
    desc = desc_tokenizer(DESCRIPTION, return_tensors="pt").to(device)
    prompt = tokenizer(PROMPT, return_tensors="pt").to(device)
    with torch.inference_mode():
        generation = model.generate(
            input_ids=desc.input_ids,
            attention_mask=desc.attention_mask,
            prompt_input_ids=prompt.input_ids,
            prompt_attention_mask=prompt.attention_mask,
        )
    report["generation_seconds"] = round(time.perf_counter() - gen_started, 2)
    peak = max(peak, proc.memory_info().rss)
    audio = generation.cpu().numpy().squeeze().astype(np.float32)
    peak_amp = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
    sf.write(str(OUT), audio, model.config.sampling_rate)
    with wave.open(str(OUT), "rb") as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
    report["wav"] = {
        "path": str(OUT),
        "bytes": OUT.stat().st_size,
        "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "channels": channels,
        "sample_width": width,
        "sample_rate": rate,
        "frames": frames,
        "duration_s": round(frames / rate, 3),
        "peak_amplitude": round(peak_amp, 5),
        "rms": round(rms, 5),
        "silent": rms < 1e-4,
        "clipped": peak_amp >= 0.99,
        "nonempty": OUT.stat().st_size > 44 and frames > 0,
    }
    report["peak_rss_mb"] = round(peak / (1024 * 1024), 1)
    report["total_seconds"] = round(time.perf_counter() - started, 2)
    report["ok"] = bool(report["wav"]["nonempty"] and not report["wav"]["silent"])
    report["tamil_readiness"] = "BLOCKED_PENDING_NATIVE_LISTENING_AND_PRONUNCIATION_REVIEW"
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows consoles may default to cp1252; keep the report printable without
    # changing the UTF-8 JSON file or the generated audio.
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
