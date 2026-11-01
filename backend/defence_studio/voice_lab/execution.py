"""Execution-provider selection for Voice Lab.

The generation provider is DirectML only when the inference environment itself
has just run a tensor on that device. A separate probe environment can prove
that the GPU accepts tensor math without making generation claim the GPU.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from defence_studio.config import PROJECT_ROOT
from defence_studio.voice_lab.common import models_root, venv_python

PREFERENCES = ("auto", "cpu", "directml")

_PROBE = r"""
import json, time
out = {"ok": False}
try:
    import torch
    import torch_directml as dml
    names = [dml.device_name(i).replace("\x00", "").strip() for i in range(dml.device_count())]
    amd = next((i for i, name in enumerate(names) if "radeon" in name.lower() or " 560" in name.lower()), None)
    if amd is None:
        raise RuntimeError("no AMD DirectML device in " + repr(names))
    device = dml.device(amd)
    x = torch.tensor([[1.0, 2.0], [3.0, 4.0]], device=device)
    y = torch.tensor([[1.0, 0.0], [0.0, 1.0]], device=device)
    got = (x @ y).cpu().tolist()
    size = 1024
    a = torch.randn(size, size, device=device)
    b = torch.randn(size, size, device=device)
    _ = a @ b
    if hasattr(getattr(torch, "dml", None), "synchronize"):
        torch.dml.synchronize()
    started = time.perf_counter()
    iters = 3
    for _i in range(iters):
        c = a @ b
    if hasattr(getattr(torch, "dml", None), "synchronize"):
        torch.dml.synchronize()
    elapsed = time.perf_counter() - started
    ops = {}
    def trial(name, fn):
        try:
            fn()
            if hasattr(getattr(torch, "dml", None), "synchronize"):
                torch.dml.synchronize()
            ops[name] = "ok"
        except Exception as exc:
            ops[name] = (type(exc).__name__ + ": " + str(exc))[:240]
    trial("relu", lambda: torch.relu(a[:64, :64]))
    trial("softmax", lambda: torch.softmax(torch.randn(32, 32, device=device), dim=-1))
    trial("conv2d", lambda: torch.nn.functional.conv2d(
        torch.randn(1, 8, 32, 32, device=device), torch.randn(8, 8, 3, 3, device=device)))
    out = {"ok": got == [[1.0, 2.0], [3.0, 4.0]], "device_index": amd, "device_names": names,
           "device_name": names[amd], "torch": torch.__version__, "matmul_n": size,
           "matmul_iters": iters, "matmul_s": round(elapsed, 4), "ops": ops, "got": got}
    if not out["ok"]:
        out["error"] = "matmul result did not match the identity"
except Exception as exc:
    out = {"ok": False, "error": (type(exc).__name__ + ": " + str(exc))[:500]}
print(json.dumps(out))
"""


def dml_python() -> Path:
    root = Path(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_DML_VENV") or (PROJECT_ROOT / ".venv-voicelab-dml"))
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def preference_path() -> Path:
    return models_root() / "execution_preference.json"


def get_preference() -> str:
    env = os.environ.get("DEFENCE_STUDIO_VOICE_LAB_DEVICE", "").strip().lower()
    if env in PREFERENCES:
        return env
    path = preference_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8")).get("provider")
    except (OSError, ValueError):
        saved = None
    return saved if saved in PREFERENCES else "auto"


def set_preference(provider: str) -> str:
    if provider not in PREFERENCES:
        raise ValueError("provider must be auto, cpu, or directml")
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": provider}, indent=2), encoding="utf-8")
    return provider


def _run_json(python: Path, code: str, timeout: int) -> dict:
    if not python.is_file():
        return {"ok": False, "error": f"interpreter missing: {python}"}
    try:
        result = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    lines = [line for line in (result.stdout or "").splitlines() if line.startswith("{")]
    if not lines:
        err = (result.stderr or result.stdout or "no output")[-300:]
        return {"ok": False, "error": err}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {"ok": False, "error": "probe output was not JSON"}


def probe_cache_path() -> Path:
    return models_root() / "directml_probe.json"


def probe_directml(python: Path | None = None, *, refresh: bool = False) -> dict:
    """Run a real matmul on the AMD DirectML device. Does not claim TTS works."""
    py = python or dml_python()
    cache = probe_cache_path()
    if not refresh and cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            age = time.time() - cache.stat().st_mtime
            if saved.get("python") == str(py) and age < 6 * 3600:
                saved["cached"] = True
                return saved
        except (OSError, ValueError):
            pass
    report = _run_json(py, _PROBE, timeout=180)
    report["python"] = str(py)
    report["kind"] = "tensor"
    report["cached"] = False
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(report), encoding="utf-8")
    except OSError:
        pass
    return report


def generation_environment() -> dict:
    py = venv_python()
    code = ("import json\n"
            "d={'python':__import__('sys').version.split()[0]}\n"
            "mods=['torch','torch_directml','parler_tts']\n"
            "found={}\n"
            "import importlib\n"
            "for name in mods:\n"
            "  try:\n"
            "    m=importlib.import_module(name); found[name]=getattr(m,'__version__','present')\n"
            "  except Exception as e:\n"
            "    found[name]=None\n"
            "d['modules']=found\n"
            "print(json.dumps(d))")
    info = _run_json(py, code, timeout=120)
    info["python_path"] = str(py)
    return info


def resolve_generation_provider(preference: str, generation_env: dict, directml_probe: dict) -> dict:
    """Choose the provider a generation job may record.

    DirectML is active only when the generation environment ran the tensor
    probe successfully. A probe from another environment is evidence about the
    GPU, not about the job.
    """
    requested = preference if preference in PREFERENCES else "auto"
    modules = (generation_env or {}).get("modules") or {}
    same_env = Path(str((directml_probe or {}).get("python") or "")) == Path(str((generation_env or {}).get("python_path") or "missing"))
    tensor_ok = bool((directml_probe or {}).get("ok")) and same_env and bool(modules.get("torch_directml"))
    if requested == "cpu":
        return {"provider": "cpu", "requested": "cpu", "fallback": False,
                "gpu_workload_verified": False,
                "reason": "CPU was selected explicitly."}
    if tensor_ok:
        return {"provider": "directml", "requested": requested, "fallback": False,
                "gpu_workload_verified": True,
                "device_name": directml_probe.get("device_name"),
                "reason": "The generation environment ran a DirectML matmul on this GPU."}
    reasons = []
    if not modules.get("torch_directml"):
        reasons.append("torch-directml is not installed in the generation environment")
    if not (directml_probe or {}).get("ok"):
        reasons.append((directml_probe or {}).get("error") or "DirectML tensor probe did not succeed in the generation environment")
    elif not same_env:
        reasons.append("DirectML tensor ops succeeded only in a separate environment, not in the generation environment")
    return {"provider": "cpu", "requested": requested, "fallback": requested == "directml" or bool((directml_probe or {}).get("ok")),
            "gpu_workload_verified": False,
            "reason": "; ".join(reasons) or "CPU is the active generation provider."}


def execution_report(*, refresh_probe: bool = True) -> dict:
    preference = get_preference()
    gen = generation_environment()
    probe_python = venv_python() if ((gen.get("modules") or {}).get("torch_directml")) else dml_python()
    probe = probe_directml(probe_python, refresh=refresh_probe) if probe_python.is_file() else {
        "ok": False, "python": str(probe_python), "error": "DirectML environment is not installed"}
    decision = resolve_generation_provider(preference, gen, probe)
    return {"preference": preference, "generation_environment": gen, "directml_probe": probe, "generation": decision}


def runner_device() -> str:
    """Value for the inference process. Never 'directml' unless this environment verified it."""
    report = execution_report(refresh_probe=False)
    if report["generation"]["provider"] == "directml" and report["generation"]["gpu_workload_verified"]:
        return "directml"
    return "cpu"
