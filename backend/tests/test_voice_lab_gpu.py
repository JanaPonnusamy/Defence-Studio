"""Execution-provider decisions. These do not download a model or claim TTS ran on a GPU."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from defence_studio.api.voice_lab_gpu_routes import register_voice_lab_gpu_routes
from defence_studio.voice_lab.execution import resolve_generation_provider, set_preference


def test_separate_directml_probe_does_not_activate_generation():
    decision = resolve_generation_provider(
        "auto",
        {"python_path": r"D:\app\.venv-voicelab\Scripts\python.exe",
         "modules": {"torch": "2.15.0+cpu", "torch_directml": None, "parler_tts": None}},
        {"ok": True, "python": r"D:\app\.venv-voicelab-dml\Scripts\python.exe", "device_name": "Radeon RX 560X"},
    )
    assert decision["provider"] == "cpu"
    assert decision["gpu_workload_verified"] is False
    assert "separate environment" in decision["reason"]


def test_explicit_directml_falls_back_when_the_generation_env_cannot_use_it():
    decision = resolve_generation_provider(
        "directml",
        {"python_path": r"D:\app\.venv\Scripts\python.exe", "modules": {"torch_directml": None}},
        {"ok": False, "python": r"D:\app\.venv\Scripts\python.exe", "error": "No module named torch_directml"},
    )
    assert decision["provider"] == "cpu"
    assert decision["fallback"] is True
    assert decision["gpu_workload_verified"] is False


def test_directml_is_active_only_after_the_generation_env_runs_the_op():
    path = r"D:\app\.venv\Scripts\python.exe"
    decision = resolve_generation_provider(
        "auto",
        {"python_path": path, "modules": {"torch_directml": "0.2.5"}},
        {"ok": True, "python": path, "device_name": "Radeon (TM) RX 560X"},
    )
    assert decision["provider"] == "directml"
    assert decision["gpu_workload_verified"] is True
    assert decision["fallback"] is False


def test_cpu_selection_stays_cpu_even_if_a_probe_succeeded():
    path = r"D:\app\.venv\Scripts\python.exe"
    decision = resolve_generation_provider(
        "cpu",
        {"python_path": path, "modules": {"torch_directml": "0.2.5"}},
        {"ok": True, "python": path},
    )
    assert decision == {"provider": "cpu", "requested": "cpu", "fallback": False,
                        "gpu_workload_verified": False, "reason": "CPU was selected explicitly."}


def test_hardware_route_reports_the_backend_decision(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFENCE_STUDIO_VOICE_LAB_MODELS", str(tmp_path))
    monkeypatch.setattr("defence_studio.api.voice_lab_gpu_routes.detect", lambda *_a, **_k: {
        "measured_at": "2026-11-01T00:00:00+00:00", "cpu": {"name": "test"}, "ram_bytes": 1,
        "ram_available_bytes": 1, "gpus": [{"name": "Radeon (TM) RX 560X", "vendor": "AMD",
                                            "vram_bytes": 4074 * 1024 * 1024, "driver": "test"}],
        "rocm_usable": False, "cuda_usable": False,
    })
    monkeypatch.setattr("defence_studio.api.voice_lab_gpu_routes.execution_report", lambda **_k: {
        "preference": "auto",
        "generation": {"provider": "cpu", "gpu_workload_verified": False, "reason": "not the generation environment"},
    })
    app = FastAPI()
    register_voice_lab_gpu_routes(app, lambda: "owner")
    body = TestClient(app).get("/api/voice-lab/hardware").json()
    assert body["active_provider"] == "cpu"
    assert body["gpu_acceleration_active"] is False
    assert body["gpus"][0]["name"].startswith("Radeon")
    set_preference("cpu")
    saved = json.loads((tmp_path / "execution_preference.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "cpu"


def test_unknown_preference_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        set_preference("cuda")
