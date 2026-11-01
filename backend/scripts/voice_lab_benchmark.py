"""VL-1 benchmark: real local inference through the production runtime (ParlerBackend).

    cd backend
    python scripts/voice_lab_benchmark.py --model parler-tts-mini-v1.1 --language en --text "..." [--db PATH --storage PATH]

Measures: cold start (process start + model load + first generation), warm repeat generation, restart
(runner killed and restarted), audio duration, real-time factor, runner RSS and CPU seconds, failures.
Every output WAV is decoded and validated (non-empty, speech detected, not clipped). Samples and the
report are registered as BENCHMARK artifacts. It never substitutes another model: it fails if the
requested one is not installed at its pinned revision.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from defence_studio.config import database_path, storage_root  # noqa: E402
from defence_studio.voice_lab import audio as A  # noqa: E402
from defence_studio.voice_lab import hardware  # noqa: E402
from defence_studio.voice_lab.common import now, sha256_file  # noqa: E402
from defence_studio.voice_lab.inference import install_status  # noqa: E402
from defence_studio.voice_lab.service import VoiceLabService  # noqa: E402


def proc_stats(pid: int | None) -> dict:
    if not pid:
        return {}
    if os.name == "nt":
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; if($p){{'{{0}} {{1}} {{2}}' -f "
                              f"$p.CPU,$p.WorkingSet64,$p.PeakWorkingSet64}}"], capture_output=True, text=True).stdout.split()
        if len(out) == 3:
            return {"cpu_s": float(out[0]), "rss_bytes": int(out[1]), "peak_rss_bytes": int(out[2])}
    return {}


def validate(path: Path) -> dict:
    x = A.decode(path, 24000)
    q = A.quality(x, 24000)
    ok = q["duration_ms"] > 300 and q["speech_ms"] > 300 and q["clipping_ratio"] < 0.001
    return {"valid": ok, "duration_ms": q["duration_ms"], "speech_ms": q["speech_ms"], "peak_dbfs": q["peak_dbfs"],
            "clipping_ratio": q["clipping_ratio"], "sha256": sha256_file(path), "bytes": path.stat().st_size}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--description", default="A female speaker delivers a clear, calm narration with moderate speed "
                                              "and pitch. The recording is very high quality with no background noise.")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--db", default=str(database_path()))
    ap.add_argument("--storage", default=str(storage_root()))
    ap.add_argument("--owner", default="voice-lab-benchmark")
    a = ap.parse_args()
    svc = VoiceLabService(a.db, a.storage, start_worker=False)
    model = svc.ctx.model(a.model)
    st = install_status(model)
    report = {"schema": "defence-studio.voice-lab-benchmark.v1", "started_at": now(), "model_id": a.model,
              "weights_repo": model["weights"]["repo"], "weights_revision": model["weights"]["revision"],
              "code_commit": model["code"]["commit"], "commercial_status": model["commercial_status"],
              "language": a.language, "text": a.text, "description": a.description, "seed": a.seed,
              "install": st, "host": {"python": platform.python_version()}, "runs": [], "failures": []}
    hw = hardware.detect(Path(a.storage), refresh=True)
    report["hardware"] = {k: hw.get(k) for k in ("cpu", "ram_bytes", "gpus", "torch", "cuda_usable", "rocm_usable")}
    if not st["installed"]:
        report["failures"].append({"stage": "install", "detail": st})
        print(json.dumps(report, indent=1, default=str))
        sys.exit(2)
    if a.language not in model["languages"]:
        sys.exit(f"{a.model} does not support language {a.language}")
    backend = svc.backends["parler"]
    cfg = {"description": a.description}
    out_dir = svc.ctx.tmp_path("")
    out_dir.mkdir(parents=True)

    def run(label, restart=False):
        if restart:
            backend.shutdown()
        out = out_dir / f"{label}.wav"
        pid_before = getattr(backend, "_proc", None).pid if getattr(backend, "_proc", None) else None
        cpu0 = proc_stats(pid_before).get("cpu_s", 0.0) if pid_before else 0.0
        t = time.perf_counter()
        try:
            meta = backend.synthesize(model, cfg, a.text, out, a.seed)
        except Exception as exc:
            report["failures"].append({"stage": label, "error": f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"})
            return None
        wall = time.perf_counter() - t
        pid = backend._proc.pid if backend._proc else None
        ps = proc_stats(pid)
        v = validate(out)
        art = svc.ctx.register_file(a.owner, out, kind="BENCHMARK", fmt="wav", duration_ms=v["duration_ms"],
                                    source={"benchmark": label, "model_id": a.model,
                                            "weights_revision": model["weights"]["revision"], "seed": a.seed})
        r = {"label": label, "wall_s": round(wall, 2), "load_s": (meta.get("load") or {}).get("load_s"),
             "model_cached": (meta.get("load") or {}).get("cached"), "gen_s": meta.get("gen_s"),
             "audio_s": meta.get("audio_s"), "rtf": meta.get("rtf"), "sample_rate": meta.get("sample_rate"),
             "execution": meta.get("execution"), "runner_pid": pid,
             # On Windows a venv python.exe is a launcher that spawns the real interpreter, so OS counters for
             # `runner_pid` describe the launcher. The interpreter reports its own RSS (psutil) in meta.
             "runner_rss_bytes_self_reported": meta.get("rss_bytes"),
             "launcher_process_stats": ps or None,
             "validation": v, "artifact_id": art["artifact_id"], "artifact_rel_path": art["rel_path"]}
        report["runs"].append(r)
        print(json.dumps({k: r[k] for k in ("label", "wall_s", "load_s", "gen_s", "audio_s", "rtf")}), flush=True)
        return r

    first = run("cold_first_run")
    second = run("warm_repeat")
    third = run("after_runner_restart", restart=True)
    backend.shutdown()
    shas = [r["validation"]["sha256"] for r in (first, second, third) if r]
    report["repeatability"] = {"same_seed_identical_audio": len(set(shas)) == 1 if len(shas) == 3 else None,
                               "note": "sampling decoder with a fixed seed"}
    report["finished_at"] = now()
    report["verdict"] = ("PASS" if len(report["runs"]) == 3 and all(r["validation"]["valid"] for r in report["runs"])
                         and not report["failures"] else "FAIL")
    data = json.dumps(report, indent=1, ensure_ascii=False, default=str).encode("utf-8")
    rep = svc.ctx.register_bytes(a.owner, data, kind="BENCHMARK", fmt="json", ext=".json",
                                 source={"benchmark_report": a.model})
    report["report_artifact"] = {"artifact_id": rep["artifact_id"], "rel_path": rep["rel_path"]}
    try:
        out_dir.rmdir()
    except OSError:
        pass
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
