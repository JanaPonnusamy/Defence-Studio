"""Hardware detection and training-capability evaluation (VL-4). Measures, never assumes."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

from defence_studio.voice_lab.common import cloud_training_enabled, now, venv_python

_CACHE: dict = {}
_LOCK = threading.Lock()
TTL_S = 120


def _run(args, timeout=20) -> str | None:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ram_bytes() -> tuple[int | None, int | None]:
    if os.name == "nt":
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return int(m.ullTotalPhys), int(m.ullAvailPhys)
        return None, None
    try:
        info = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in Path("/proc/meminfo").read_text().splitlines()}
        return info.get("MemTotal"), info.get("MemAvailable")
    except OSError:
        return None, None


def _windows_cim() -> dict:
    ps = ("$c=Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors;"
          "$g=@(Get-CimInstance Win32_VideoController | Select-Object Name,AdapterCompatibility,AdapterRAM,DriverVersion);"
          "@{cpu=$c;gpus=$g} | ConvertTo-Json -Depth 4 -Compress")
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=30)
    try:
        return json.loads(out) if out else {}
    except ValueError:
        return {}


def _nvidia() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    out = _run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    gpus = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            gpus.append({"name": parts[0], "vendor": "NVIDIA", "vram_bytes": int(float(parts[1])) * 1024 ** 2,
                         "driver": parts[2], "source": "nvidia-smi"})
    return gpus


def _torch_probe() -> dict:
    py = venv_python()
    if not py.is_file():
        return {"installed": False, "reason": f"Voice Lab environment not found ({py.parent.parent.name})"}
    code = ("import json,sys\ntry:\n import torch\n d={'installed':True,'version':torch.__version__,"
            "'cuda_available':torch.cuda.is_available(),'cuda_version':torch.version.cuda,"
            "'hip_version':getattr(torch.version,'hip',None),'device_count':torch.cuda.device_count() if torch.cuda.is_available() else 0}\n"
            "except Exception as e:\n d={'installed':False,'reason':repr(e)[:200]}\nprint(json.dumps(d))")
    out = _run([str(py), "-I", "-c", code], timeout=90)
    try:
        return json.loads(out.strip().splitlines()[-1]) if out else {"installed": False, "reason": "probe failed"}
    except (ValueError, IndexError):
        return {"installed": False, "reason": "probe output unreadable"}


def detect(storage_root: Path, *, refresh: bool = False) -> dict:
    with _LOCK:
        hit = _CACHE.get("hw")
        if hit and not refresh and time.time() - hit[0] < TTL_S:
            return hit[1]
    cim = _windows_cim() if os.name == "nt" else {}
    cpu = cim.get("cpu") or {}
    total, avail = _ram_bytes()
    gpus = []
    nv = _nvidia()
    for g in (cim.get("gpus") or []):
        if isinstance(g, dict) and g.get("Name"):
            vendor = ("NVIDIA" if "nvidia" in g["Name"].lower() else
                      "AMD" if any(k in g["Name"].lower() for k in ("radeon", "amd")) else
                      "Intel" if "intel" in g["Name"].lower() else (g.get("AdapterCompatibility") or "unknown"))
            gpus.append({"name": g["Name"], "vendor": vendor,
                         "vram_bytes": int(g["AdapterRAM"]) if g.get("AdapterRAM") else None,
                         "vram_note": "Win32 AdapterRAM caps at 4 GiB" if g.get("AdapterRAM") else None,
                         "driver": g.get("DriverVersion"), "source": "Win32_VideoController"})
    for g in nv:  # nvidia-smi is authoritative for NVIDIA VRAM
        gpus = [x for x in gpus if not (x["vendor"] == "NVIDIA" and x["name"] in g["name"])] + [g]
    disks = []
    for p in {Path(storage_root).resolve().anchor or "/", str(Path(storage_root).resolve())}:
        try:
            u = shutil.disk_usage(p)
            disks.append({"path": p, "total_bytes": u.total, "free_bytes": u.free})
        except OSError:
            pass
    torch_info = _torch_probe()
    hw = {
        "measured_at": now(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": {"name": cpu.get("Name") or platform.processor() or None,
                "cores": cpu.get("NumberOfCores"), "threads": cpu.get("NumberOfLogicalProcessors") or os.cpu_count()},
        "ram_bytes": total, "ram_available_bytes": avail,
        "gpus": gpus,
        "nvidia_smi": bool(shutil.which("nvidia-smi")),
        "rocm_smi": bool(shutil.which("rocm-smi")),
        "torch": torch_info,
        "cuda_usable": bool(torch_info.get("cuda_available")) and not torch_info.get("hip_version"),
        "rocm_usable": bool(torch_info.get("cuda_available")) and bool(torch_info.get("hip_version")),
        "disks": disks,
        "cloud_training_enabled": cloud_training_enabled(),
    }
    with _LOCK:
        _CACHE["hw"] = (time.time(), hw)
    return hw


def training_capability(hw: dict, model: dict, *, target: str = "local", storage_free: int | None = None) -> dict:
    """Return {supported, target, blockers:[{code,detail}], requirements}. Never optimistic."""
    req = (model.get("training") or {})
    blockers = []
    if not req.get("supported"):
        blockers.append({"code": "MODEL_NOT_TRAINABLE", "detail": req.get("reason") or "model has no training path"})
        return {"supported": False, "target": target, "blockers": blockers, "requirements": req}
    if target == "cloud":
        if not cloud_training_enabled():
            blockers.append({"code": "CLOUD_DISABLED",
                             "detail": "Cloud training is disabled. The owner must set DEFENCE_STUDIO_VOICE_LAB_CLOUD_TRAINING=1 "
                                       "and configure a cloud worker; paid resources are never used by default."})
        elif not os.environ.get("DEFENCE_STUDIO_VOICE_LAB_CLOUD_WORKER_URL"):
            blockers.append({"code": "CLOUD_WORKER_NOT_CONFIGURED",
                             "detail": "Set DEFENCE_STUDIO_VOICE_LAB_CLOUD_WORKER_URL to an owner-provisioned worker"})
        return {"supported": not blockers, "target": target, "blockers": blockers, "requirements": req}
    need = req.get("requires")
    gb = 1024 ** 3
    if need == "cuda" and not hw.get("cuda_usable"):
        vendors = sorted({g["vendor"] for g in hw.get("gpus", [])}) or ["none"]
        blockers.append({"code": "NO_CUDA_GPU",
                         "detail": f"Training needs an NVIDIA CUDA GPU; detected GPU vendors: {', '.join(vendors)}; "
                                   f"torch CUDA available: {bool(hw.get('torch', {}).get('cuda_available'))}"})
    if req.get("min_vram_gb"):
        vram = max([g.get("vram_bytes") or 0 for g in hw.get("gpus", []) if g["vendor"] == "NVIDIA"] or [0])
        if vram < req["min_vram_gb"] * gb:
            blockers.append({"code": "INSUFFICIENT_VRAM",
                             "detail": f"needs >= {req['min_vram_gb']} GB NVIDIA VRAM; detected {round(vram / gb, 1)} GB"})
    if req.get("min_ram_gb") and (hw.get("ram_bytes") or 0) < req["min_ram_gb"] * gb * 0.95:
        blockers.append({"code": "INSUFFICIENT_RAM",
                         "detail": f"needs >= {req['min_ram_gb']} GB RAM; detected {round((hw.get('ram_bytes') or 0) / gb, 1)} GB"})
    free = storage_free if storage_free is not None else max([d["free_bytes"] for d in hw.get("disks", [])] or [0])
    if req.get("min_disk_gb") and free < req["min_disk_gb"] * gb:
        blockers.append({"code": "INSUFFICIENT_DISK",
                         "detail": f"needs >= {req['min_disk_gb']} GB free; {round(free / gb, 1)} GB free"})
    return {"supported": not blockers, "target": target, "blockers": blockers, "requirements": req}
