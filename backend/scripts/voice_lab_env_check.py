"""Voice Lab environment check (read-only). Prints JSON; exit 0 = inference-ready for at least one model.

    cd backend && python scripts/voice_lab_env_check.py

Never prints secrets: for the Hugging Face token it reports only whether one is set.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from defence_studio.config import storage_root  # noqa: E402
from defence_studio.voice_lab import hardware  # noqa: E402
from defence_studio.voice_lab.common import models_config_path, models_root, venv_python  # noqa: E402
from defence_studio.voice_lab.inference import install_status  # noqa: E402


def main():
    hw = hardware.detect(storage_root(), refresh=True)
    models = json.loads(models_config_path().read_text(encoding="utf-8"))["models"]
    token_set = bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
                     or (Path.home() / ".cache" / "huggingface" / "token").is_file())
    checks = {
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which("ffmpeg") or os.environ.get("VOICE_FFMPEG"),
        "ffmpeg_rubberband": None,
        "voice_lab_venv": str(venv_python()) if venv_python().is_file() else None,
        "models_root": str(models_root()),
        "hf_token_present": token_set,
        "yt_dlp": shutil.which("yt-dlp"),
        "cloud_training_enabled": hw["cloud_training_enabled"],
    }
    if checks["ffmpeg"]:
        import subprocess
        out = subprocess.run([checks["ffmpeg"], "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        checks["ffmpeg_rubberband"] = " rubberband " in out
    status = []
    for m in models:
        st = install_status(m) if m.get("runtime") else {"installed": False, "reason": "no integrated runtime"}
        cap = hardware.training_capability(hw, m)
        status.append({"model_id": m["model_id"], "commercial_status": m["commercial_status"], "installed": st["installed"],
                       "gated": m["weights"].get("gated"), "blocked_on_token": bool(m["weights"].get("gated")) and not token_set,
                       "training_supported_here": cap["supported"], "training_blockers": [b["code"] for b in cap["blockers"]]})
    out = {"hardware": {k: hw[k] for k in ("cpu", "ram_bytes", "gpus", "torch", "cuda_usable", "rocm_usable", "disks")},
           "checks": checks, "models": status,
           "inference_ready": any(s["installed"] for s in status)}
    print(json.dumps(out, indent=1, default=str))
    sys.exit(0 if out["inference_ready"] else 1)


if __name__ == "__main__":
    main()
