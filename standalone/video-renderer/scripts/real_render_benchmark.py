"""Real acceptance renders through the job system (nothing mocked), with wall time and peak memory.

    python scripts/real_render_benchmark.py small   # 20 s, 4 shots, 2 transitions, overlays, 1 GAP, 1920x1080@25
    python scripts/real_render_benchmark.py long    # ~10 min, ~140 shots (VVM-style synthetic), 1920x1080@25

Writes storage/benchmarks/<name>/result.json and prints a summary. Package + output stay under storage/.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renderer.config import Settings  # noqa: E402
from renderer.db import make_session_factory  # noqa: E402
from renderer.jobs import JobService  # noqa: E402
from renderer.media import peak_memory_mb  # noqa: E402
from renderer.synthetic import long_package, small_package  # noqa: E402

PROFILE = {"width": 1920, "height": 1080, "fps": 25}


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "small"
    if which not in ("small", "long"):
        raise SystemExit("usage: real_render_benchmark.py small|long")
    settings = Settings()
    root = settings.storage_root / "benchmarks" / which
    shutil.rmtree(root, ignore_errors=True)
    settings.storage_root = root  # isolated DB / renders per benchmark
    settings.ensure_dirs()
    pkg = root / "package"
    t_build = time.perf_counter()
    (small_package(pkg, profile=PROFILE) if which == "small" else long_package(pkg, profile=PROFILE))
    build_s = time.perf_counter() - t_build
    svc = JobService(settings, make_session_factory(settings.db_path))
    t0 = time.perf_counter()
    job, _ = svc.create(str(pkg))
    last = -1.0
    while True:
        j = svc.get(job.job_id)
        if j.status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if j.progress - last >= 0.1:
            print(f"  {j.status} {j.progress:.0%} ({j.frames_done}/{j.frames_total})", flush=True)
            last = j.progress
        time.sleep(1)
    wall = time.perf_counter() - t0
    svc.shutdown(wait=False)
    if j.status != "SUCCEEDED":
        print("FAILED:", j.error_code, j.error_message)
        return 1
    qc = json.loads(Path(j.qc_path).read_text(encoding="utf-8"))
    result = {
        "package": str(pkg), "output": j.output_path, "package_build_s": round(build_s, 1), "render_wall_s": round(wall, 1),
        "output_duration_s": float(qc["probe"]["format"]["duration"]), "frames": qc["decode"]["frames"],
        "realtime_factor": round(wall / float(qc["probe"]["format"]["duration"]), 2),
        "size_mb": round(qc["output"]["size_bytes"] / 1048576, 1), "sha256": qc["output"]["sha256"],
        "peak_memory_mb": {**qc["memory_peak_mb"], "this_process_now": peak_memory_mb()},
        "shots": qc["package"]["shots"], "gap_shots": qc["package"]["gap_shots"],
        "probe": qc["probe"], "checks": {c["name"]: c["ok"] for c in qc["checks"]},
        "warnings": [(w["code"], w["count"]) for w in qc["warnings"]], "timings_s": qc["timings_s"],
    }
    (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if all(result["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
