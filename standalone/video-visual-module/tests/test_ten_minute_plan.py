"""Spec §9/§10 at representative scale: ~10-minute narration through the public API."""
from __future__ import annotations

import time

from fixtures.episode_loader import load_episode
from tests.conftest import wait_job


def test_ten_minute_episode_plan(client):
    pid = client.post("/projects", json={"name": "ten-minute", "ai_image_budget_usd": 5}).json()["project_id"]
    ep = load_episode(client, pid)
    sc = client.post("/scenes", json={"project_id": pid, "narration": ep["narration"], "replace_existing": True}).json()
    seconds = sc["scenes"][-1]["end_time"]
    assert 540 <= seconds <= 720, seconds  # ~9-12 minutes of estimated narration

    t0 = time.perf_counter()
    job = client.post("/visual-plans/build", json={"project_id": pid, "options": {
        "generate_ai": True, "ai_provider": "local-procedural", "max_ai_images": 4}}).json()  # legacy alias
    done = wait_job(client, job["job_id"], timeout=300)
    elapsed = time.perf_counter() - t0
    assert done["status"] == "SUCCEEDED", done
    st = client.get(f"/visual-plans/{done['result_id']}?include_timeline=false").json()["stats"]
    assert st["shot_count"] >= 100                       # "100+ rendered shots possible"
    assert 35 <= st["distinct_asset_count"] <= 60       # "approximately 35-60 distinct visual assets"
    assert st["images_generated_in_plan"] <= 4           # 3-4 hook images, never 40-60
    # Phase 2 truthfulness: the bundled local provider makes PLACEHOLDERS, which are never counted as AI
    assert st["placeholder_images_generated_in_plan"] == st["images_generated_in_plan"] > 0
    assert st["real_ai_images_generated_in_plan"] == 0 and st["ai_asset_count"] == 0
    assert st["reused_asset_count"] >= 20                # generate once, reuse intelligently
    assert st["visual_change_count"] < st["shot_count"]  # cuts are narration-driven; extra shots are reframes
    assert elapsed < 60, elapsed
    costs = client.get(f"/costs/{pid}").json()
    assert costs["ai_image_count"] == 0 and costs["placeholder_image_count"] == st["images_generated_in_plan"]
    assert costs["ai_generation_cost"] == 0 and costs["totals_complete"] is True
    assert costs["cost_per_finished_minute"] is None     # no real AI spend -> no invented per-minute cost
