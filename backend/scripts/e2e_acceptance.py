"""End-to-end acceptance run: real feeds + real sources + the three real
standalone modules, driven through the core services on a throw-away database.

What is REAL: RSS discovery and article fetch over the network, extraction and
analysis, the Visual / Audio / Renderer modules over HTTP, FFmpeg encoding,
subtitle burn-in, QC measurements, branding/thumbnail/package build.
What is SIMULATED and labelled so in the report: the human review decisions
(this harness acts as the reviewer/owner), the script text (the offline
extractive provider restates approved claims; no LLM is called), and the
rights-cleared still (a synthetic image the harness itself creates).
What is NOT done: any YouTube call (no credentials) and any publication.

Usage: python scripts/e2e_acceptance.py [topic] [--out report.json]
Requires the Visual (8765), Audio (8001) and Renderer (8780) modules running.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DEFENCE_STUDIO_AUTH_SECRET", "phase-one-test-signing-secret-32-bytes-minimum")
os.environ["DEFENCE_STUDIO_SCRIPT_PROVIDER"] = "offline"

from PIL import Image  # noqa: E402

from defence_studio.database import initialize_database  # noqa: E402
from defence_studio.research_worker import build_research_worker  # noqa: E402
from defence_studio.services.discovery import DiscoveryApiService  # noqa: E402
from defence_studio.services.jobs import JobService  # noqa: E402
from defence_studio.services.review import ReviewService  # noqa: E402
from defence_studio.services.worker import WorkerService  # noqa: E402
from defence_studio.services.audio import AudioService  # noqa: E402
from defence_studio.services.evidence import EvidenceService  # noqa: E402
from defence_studio.services.render import RenderService  # noqa: E402
from defence_studio.services.visual import VisualService  # noqa: E402
from defence_studio.services.script import ScriptService  # noqa: E402
from defence_studio.services.youtube import YouTubeService  # noqa: E402

OWNER = "e2e-acceptance"
REPORT: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": [], "simulated": [
    "human review decisions (harness acts as owner/reviewer)",
    "script text: offline extractive provider restating approved claims, no LLM",
    "rights-cleared still: synthetic image created by the harness"], "not_done": []}


def step(name):
    def deco(fn):
        def run(*a, **kw):
            t0 = time.time()
            try:
                out = fn(*a, **kw)
                REPORT["steps"].append({"step": name, "ok": True, "seconds": round(time.time() - t0, 1), **(out or {})})
                print(f"[ok]   {name}  ({time.time() - t0:.1f}s)  {out or ''}")
                return out
            except Exception as exc:
                REPORT["steps"].append({"step": name, "ok": False, "seconds": round(time.time() - t0, 1),
                                        "error": f"{type(exc).__name__}: {exc}"})
                print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                raise
        return run
    return deco


def main() -> int:
    topic = next((a for a in sys.argv[1:] if not a.startswith("--")), "Ukraine")
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT.parent / "analysis" / "e2e-acceptance.json"
    base = Path(os.environ.get("E2E_DIR", "C:/dstmp/e2e"))
    base.mkdir(parents=True, exist_ok=True)
    db, root = base / f"e2e-{int(time.time())}.sqlite3", base / "store"
    initialize_database(db)
    jobs = JobService(db)
    job = jobs.create(OWNER, f"e2e-{time.time()}", topic, "en")["job_id"]
    REPORT.update(job_id=job, topic=topic, database=str(db))
    svc = YouTubeService(db, root)   # qc / package / youtube / asset + integration reads
    audio_svc, visual_svc, render_svc = AudioService(db, root), VisualService(db, root), RenderService(db, root)

    @step("discovery: real RSS feeds")
    def discover():
        d = DiscoveryApiService(db, root)
        d.create_request(job, OWNER, {"topic": topic, "language": "en", "limits": {"max_results": 12}})
        WorkerService(db).queue(job, OWNER)
        w = build_research_worker(db, root)
        w.run_once(); w.run_once()
        res = d.list_candidates(job, OWNER)
        c = res["candidates"]
        assert c, "no candidates from live feeds"
        pick = [x for x in c if "bbc" not in (x["declared_metadata"].get("publisher") or "").lower()][:2] or c[:2]
        d.select_candidates(job, OWNER, [x["candidate_id"] for x in pick])
        return {"candidates": len(c), "selected": [x["canonical_url"] for x in pick],
                "providers": [(p["provider"], p["status"]) for p in res["providers"]]}

    @step("acquisition + analysis: real article fetch")
    def research():
        w = build_research_worker(db, root)
        ran = []
        while w.run_once():
            ran.append(1)
        state = jobs.get(job, OWNER)
        stages = {s["stage_key"]: s["status"] for s in state["stages"]}
        review = ReviewService(db, root)
        claims = review.list_claims(job, OWNER)
        return {"stages": stages, "sources": len(review.list_sources(job, OWNER)["sources"]),
                "claims_extracted": len(claims["claims"])}

    @step("evidence review (simulated reviewer) + package approval")
    def evidence():
        review = ReviewService(db, root)
        sources = review.list_sources(job, OWNER)
        claims = review.list_claims(job, OWNER)
        for s in sources["sources"]:
            if s["status"] == "ACQUIRED":
                review.decide_source(job, OWNER, s["source_id"], "APPROVED_FOR_USE",
                                     sources["acquisition_artifact_sha256"], 0,
                                     "E2E harness: simulated reviewer approval", s["source_id"])
        approved = 0
        for i, c in enumerate(claims["claims"]):
            if i < 4:
                review.decide_claim(job, OWNER, c["claim_id"], "APPROVED_FOR_USE", claims["analysis_artifact_sha256"], 0,
                                    "E2E harness: simulated passage inspection", c["claim_id"])
                approved += 1
            else:  # policy: every claim needs a final decision; the rest are excluded from the video
                review.decide_claim(job, OWNER, c["claim_id"], "REJECTED", claims["analysis_artifact_sha256"], 0,
                                    "E2E harness: not used in this short video", c["claim_id"])
        ev = EvidenceService(db, root)
        draft = ev.draft(job, OWNER)
        assert draft["package"]["ready"], draft["package"]["blockers"]
        ev.approve(job, OWNER, draft["revision"], "E2E harness: simulated approval", "e2e-ev")
        return {"claims_approved": approved, "eligible": len(draft["package"]["eligible_claim_ids"])}

    @step("script: offline extractive provider, validation, approval")
    def script():
        cfg = {"language": "en", "audience": "general", "tone": "neutral", "script_format": "narration",
               "target_duration_seconds": 120, "target_words": 150, "model": "offline-extractive",
               "prompt_version": "defence-studio.source-grounded-script.v1", "max_text_cost_inr": 10}
        sv = ScriptService(db, root)  # separate instance: SceneService.generate shadows it on the combined class
        rev = sv.generate(job, OWNER, cfg, "e2e-script")
        assert rev["validation"]["passed"], rev["validation"]["issues"]
        sha = next(r["revision_sha256"] for r in sv.list_revisions(job, OWNER, "en")["revisions"]
                   if r["version"] == rev["version"])
        sv.approve(job, OWNER, "en", rev["version"], sha, "E2E harness approval", "e2e-script-ap")
        return {"words": rev["word_count"]["words"], "provider": rev["usage"]["provider"],
                "warnings": [i["code"] for i in rev["validation"]["issues"]]}

    # SceneService.generate and ScriptService.generate share a name; address the scene one explicitly.
    from defence_studio.services.scene import SceneService  # noqa: E402

    @step("scene plan: generate + approve")
    def scene_plan():
        sc = SceneService(db, root)
        plan = sc.generate(job, OWNER, {"language": "en", "target_total_seconds": 120, "words_per_minute": 150,
                                        "default_transition": "cut"}, "e2e-scene")
        row = next(r for r in sc.list_plans(job, OWNER, "en")["plans"] if r["version"] == plan["version"])
        sc.approve(job, OWNER, "en", plan["version"], row["plan_sha256"], "E2E harness approval", "e2e-scene-ap")
        return {"scenes": len(plan["scenes"])}

    @step("assets: ingest, rights review, assign (synthetic rights-cleared still)")
    def assets():
        buf = io.BytesIO()
        Image.new("RGB", (1280, 720), (24, 52, 92)).save(buf, "PNG")
        a = svc.ingest_asset_upload(job, OWNER, buf.getvalue(),
                                    {"title": "Briefing backdrop (synthetic)", "creator": "e2e harness",
                                     "documented_origin": "generated by the acceptance harness"}, "e2e-asset")
        rights = {"schema_version": "defence-studio.asset-rights.v1", "rights_basis": "creator_owned",
                  "attribution_required": False, "permitted_uses": ["broadcast", "web"], "commercial_use": "allowed"}
        r = svc.set_rights(job, OWNER, a["asset_id"], rights, "e2e-rights")
        svc.review_asset(job, OWNER, a["asset_id"], "APPROVED", r["asset_sha256"], r["rights_sha256"], 0,
                         "E2E harness: synthetic image owned by the harness", "e2e-asset-rv")
        plan = next(p for p in svc.list_plans(job, OWNER, "en")["plans"] if p["status"] == "APPROVED")
        body = svc.get_plan(job, OWNER, "en", plan["version"])
        for i, s in enumerate(body["scenes"]):
            svc.assign_asset(job, OWNER, "en", s["scene_id"], a["asset_id"], f"e2e-assign-{i}")
        return {"assigned_scenes": len(body["scenes"])}

    @step("audio: real Audio Module (local OS English voice)")
    def audio():
        item = audio_svc.start_audio(job, OWNER, "en", "e2e-audio", align=False, background=False)
        assert item["status"] == "SUCCEEDED", item["detail"]
        body = svc.read_integration_body(job, OWNER, "en", "audio_set", item["version"])
        return {"version": item["version"], "scenes": body["summary"]["scene_count"],
                "seconds": body["summary"]["total_duration_seconds"], "mock": body["summary"]["any_mock"]}

    @step("visual: real Visual Module, measured timing, imported asset")
    def visual():
        a = [i for i in svc._integration_rows(job, "en", "audio_set") if i["status"] == "SUCCEEDED"][-1]
        item = visual_svc.start_visual_plan(job, OWNER, "en", "e2e-visual",
                                     {"output_width": 1280, "output_height": 720, "frame_rate": 25},
                                     background=False, audio_version=a["version"])
        assert item["status"] == "SUCCEEDED", item["detail"]
        body = svc.read_integration_body(job, OWNER, "en", "visual_timeline", item["version"])
        return {"version": item["version"], "shots": body["summary"]["shot_count"], "gaps": body["summary"]["gap_count"],
                "assignments_honored": sum(1 for r in body["assignments"] if r["honored"])}

    @step("render: real Renderer + FFmpeg + subtitles")
    def render():
        a = [i for i in svc._integration_rows(job, "en", "audio_set") if i["status"] == "SUCCEEDED"][-1]
        v = [i for i in svc._integration_rows(job, "en", "visual_timeline") if i["status"] == "SUCCEEDED"][-1]
        item = render_svc.start_render(job, OWNER, "en", "e2e-render", audio_version=a["version"],
                                visual_version=v["version"], background=False)
        assert item["status"] == "SUCCEEDED", item["detail"]
        body = svc.read_integration_body(job, OWNER, "en", "render", item["version"])
        return {"version": item["version"], "seconds": body["audio_duration_seconds"],
                "probe": body["video_subtitled"]["probe"]["video"], "cues": body["subtitles"]["cue_count"]}

    @step("qc + final approval")
    def qc():
        r = [i for i in svc._integration_rows(job, "en", "render") if i["status"] == "SUCCEEDED"][-1]
        q = svc.start_qc(job, OWNER, "en", r["version"], "e2e-qc", background=False)
        assert q["status"] == "SUCCEEDED", q["detail"]
        report = svc.read_report(job, OWNER, "en", q["version"])
        checks = {c["id"]: c["status"] for c in report["checks"]}
        assert report["summary"]["passed"], {k: v for k, v in checks.items() if v == "FAIL"}
        warns = sorted(c["id"] for c in report["checks"] if c["status"] == "WARN")
        svc.approve_render(job, OWNER, "en", q["version"], "E2E harness approval", warns, "e2e-qc-ap")
        return {"qc_version": q["version"], "checks": checks, "loudness": report["measurements"]["analysis"]["integrated_lufs"]}

    @step("package: branding, thumbnail, metadata, source register")
    def package():
        item = svc.start_package(job, OWNER, "en", "e2e-pkg", background=False)
        assert item["status"] == "AWAITING_REVIEW", item["detail"]
        body = svc.read_integration_body(job, OWNER, "en", "publish_package", item["version"])
        svc.review_package(job, OWNER, "en", item["version"], "APPROVED", "E2E harness approval")
        return {"version": item["version"], "title": body["metadata"]["title"],
                "sources": body["source_register_count"], "duration": body["files"]["video_branded"]["probe"]["duration"],
                "thumbnail_ok": body["files"]["thumbnail"]["within_youtube_limit"]}

    @step("youtube: configuration check only (no credentials, nothing uploaded)")
    def youtube():
        cfg = svc.config_status()
        REPORT["not_done"].append("YouTube OAuth/upload: " + ("blocked, missing " + ", ".join(cfg["missing"])
                                                              if cfg["missing"] else "not exercised by this harness"))
        return {"configured": cfg["configured"], "missing": cfg["missing"]}

    @step("persistence: fresh service instances read the same state")
    def persistence():
        fresh = YouTubeService(db, root)
        n = fresh.recover_interrupted()
        status = fresh.package_status(job, OWNER, "en")
        gate = fresh.approved_render(job, OWNER, "en")
        return {"recovered_rows": n, "package_status": status["items"][-1]["status"], "approved_render": gate["render_version"]}

    code = 0
    try:
        for fn in (discover, research, evidence, script, scene_plan, assets, audio, visual, render, qc, package,
                   youtube, persistence):
            fn()
    except Exception:
        code = 1
    REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT["ok"] = code == 0 and all(s["ok"] for s in REPORT["steps"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")
    print("report ->", out_path, "OK" if REPORT["ok"] else "FAILED")
    return code


if __name__ == "__main__":
    sys.exit(main())
