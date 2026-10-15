from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from renderer.config import Settings
from renderer.db import TERMINAL, make_session_factory
from renderer.jobs import JobService
from renderer.synthetic import small_package


out = Path(os.environ["E2E_OUT"]).resolve()
work = out / "synthetic-render-work"
work.mkdir(parents=True, exist_ok=True)
settings = Settings(storage_root=work / "storage", workers=1)
settings.ensure_dirs()
pkg = small_package(work / "package", profile={"width": 320, "height": 180, "fps": 12})
svc = JobService(settings, make_session_factory(settings.db_path))
started = time.monotonic()
job, created = svc.create(str(pkg), idempotency_key="final-e2e-synthetic-render")
while time.monotonic() - started < 120:
    row = svc.get(job.job_id)
    if row.status in TERMINAL:
        break
    time.sleep(0.2)
row = svc.get(job.job_id)
result = {"created": created, "job": row.snapshot(), "storage_root": str(settings.storage_root)}
if row.output_path and Path(row.output_path).is_file():
    shutil.copy2(row.output_path, out / "synthetic-render.mp4")
if row.qc_path and Path(row.qc_path).is_file():
    shutil.copy2(row.qc_path, out / "synthetic-render-qc.json")
svc.shutdown(wait=True)
(out / "real-render-e2e.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if row.status == "SUCCEEDED" else 1)
