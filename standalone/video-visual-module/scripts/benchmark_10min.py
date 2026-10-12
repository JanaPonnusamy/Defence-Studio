"""Measure planning a representative ~10-minute narration (spec §9/§10).

Runs fully in-process against an ISOLATED temporary storage root (never the
module's real storage/), through the public API. Prints JSON with measured
numbers; optionally writes it to --out.

    .venv\\Scripts\\python.exe scripts\\benchmark_10min.py --out bench.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--keep", action="store_true", help="keep the temp storage root")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="vvm_bench_"))
    os.environ["VIDEO_MODULE_STORAGE_ROOT"] = str(tmp)
    os.environ.pop("VIDEO_MODULE_DATABASE_URL", None)
    os.environ["VIDEO_MODULE_LOG_LEVEL"] = "WARNING"

    from fastapi.testclient import TestClient

    from app.main import app
    from fixtures.episode_loader import load_episode

    def wait(c, job_id, timeout=600):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            j = c.get(f"/jobs/{job_id}").json()
            if j["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                return j
            time.sleep(0.02)
        raise TimeoutError(job_id)

    res: dict = {}
    try:
        with TestClient(app) as c:
            pid = c.post("/projects", json={"name": "10-minute benchmark", "ai_image_budget_usd": 5}).json()["project_id"]
            t0 = time.perf_counter()
            ep = load_episode(c, pid)
            res["asset_import_seconds"] = round(time.perf_counter() - t0, 3)
            res["assets_available"] = ep["asset_total"]
            res["asset_kinds"] = ep["asset_counts"]
            res["narration_words"] = len(ep["narration"].split())

            t0 = time.perf_counter()
            sc = c.post("/scenes", json={"project_id": pid, "narration": ep["narration"], "replace_existing": True}).json()
            res["scene_parse_seconds"] = round(time.perf_counter() - t0, 3)
            res["scene_count"] = sc["scene_count"]
            res["segment_count"] = sum(len(s["narration_segments"]) for s in sc["scenes"])
            res["estimated_narration_seconds"] = sc["scenes"][-1]["end_time"]

            for label, options in (("plan_no_ai", {"generate_ai": False}),
                                   # Phase 2: the bundled local generator makes PLACEHOLDER images (not AI)
                                   ("plan_with_placeholders", {"generate_ai": True, "ai_provider": "placeholder-procedural",
                                                               "max_ai_images": 4})):
                t0 = time.perf_counter()
                job = c.post("/visual-plans/build", json={"project_id": pid, "options": options}).json()
                done = wait(c, job["job_id"])
                elapsed = time.perf_counter() - t0
                entry = {"job_status": done["status"], "wall_seconds_submit_to_succeeded": round(elapsed, 3),
                         "error": done["error_code"]}
                if done["status"] == "SUCCEEDED":
                    plan = c.get(f"/visual-plans/{done['result_id']}?include_timeline=false").json()
                    st = plan["stats"]
                    entry.update({k: st[k] for k in (
                        "shot_count", "visual_change_count", "distinct_asset_count", "source_asset_count", "ai_asset_count",
                        "ai_images_generated_in_plan", "real_ai_images_generated_in_plan",
                        "placeholder_images_generated_in_plan", "reused_asset_count", "max_shots_per_asset", "gap_count",
                        "gaps_by_code", "avg_shot_duration_s", "total_duration_s", "fallback_duration_splits",
                        "shots_by_visual_type", "visual_change_reasons")})
                    entry["warnings"] = plan["warnings"]
                    tl = c.get(f"/visual-plans/{done['result_id']}/timeline")
                    entry["timeline_json_bytes"] = len(tl.content)
                res[label] = entry
            costs = c.get(f"/costs/{pid}").json()
            res["costs"] = {k: costs[k] for k in ("ai_image_count", "ai_generation_cost", "cost_per_generated_image",
                                                  "finished_minutes", "cost_per_finished_minute", "source_asset_count",
                                                  "total_visual_asset_count", "placeholder_image_count",
                                                  "unknown_cost_count", "totals_complete")}
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    text = json.dumps(res, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
