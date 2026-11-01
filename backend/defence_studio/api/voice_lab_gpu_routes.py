"""Owner-facing hardware and execution-provider report.

The browser displays this payload. It does not detect the GPU.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from defence_studio.voice_lab.execution import PREFERENCES, execution_report, set_preference
from defence_studio.voice_lab.hardware import detect
from defence_studio.config import PROJECT_ROOT


class ProviderBody(BaseModel):
    provider: str = Field(pattern="^(auto|cpu|directml)$")


def register_voice_lab_gpu_routes(app: FastAPI, require_owner) -> None:
    @app.get("/api/voice-lab/hardware")
    def hardware(_owner: str = Depends(require_owner)):
        hw = detect(PROJECT_ROOT / "data")
        report = execution_report(refresh_probe=False)
        gpus = []
        for gpu in hw.get("gpus") or []:
            gpus.append({"name": gpu.get("name"), "vendor": gpu.get("vendor"),
                         "vram_bytes": gpu.get("vram_bytes"), "driver": gpu.get("driver")})
        return {
            "measured_at": hw.get("measured_at"),
            "cpu": hw.get("cpu"),
            "ram_bytes": hw.get("ram_bytes"),
            "ram_available_bytes": hw.get("ram_available_bytes"),
            "gpus": gpus,
            "rocm_usable": bool(hw.get("rocm_usable")),
            "cuda_usable": bool(hw.get("cuda_usable")),
            "execution": report,
            "active_provider": report["generation"]["provider"],
            "gpu_acceleration_active": bool(report["generation"].get("gpu_workload_verified")),
            "allowed_preferences": list(PREFERENCES),
        }

    @app.put("/api/voice-lab/hardware/provider")
    def choose(body: ProviderBody, _owner: str = Depends(require_owner)):
        set_preference(body.provider)
        return {"provider": body.provider, "execution": execution_report(refresh_probe=False)}
