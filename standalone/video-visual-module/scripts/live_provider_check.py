"""
Live OpenAI provider check for the Video Visual Module -- see docs/LIVE_PROVIDER_TEST_RUNBOOK.md.

NOT part of pytest (pytest only collects tests/). Every command runs the module IN-PROCESS against a
dedicated, throwaway storage root under %TEMP%\\vvm_live_check\\<run-id> -- never the module's real storage/.

Commands (default: dry-run):
  dry-run          (default) print the plan, key presence (boolean + length only), enablement flags and
                   the documented cost estimate. NO network.
  status           module provider status, configuration only. NO network.
  probe            NON-BILLABLE: GET /v1/models/{model} through the module (needs a key).
  invalid-key      NON-BILLABLE: uses a deliberately invalid key (your real key is not used) for the
                   models probe and one generation request; both must fail with an auth error.
  generate         *** BILLABLE: exactly ONE image. *** Requires ALL of:
                     - env VVM_ENABLE_PAID_PROVIDER_TESTS=1
                     - the --confirm-paid flag
                     - documented pre-call estimate <= --max-usd (default 0.01)
                   Retries are forced to 0, and a guard transport refuses any second generation POST.
                   Then validates the image, metadata, cost status, asset record, file, checksum.
  cleanup --run-id ID   delete ONLY that run's throwaway directory (verified by its manifest).

The API key is never printed, logged or written to the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = Path(tempfile.gettempdir()) / "vvm_live_check"
MANIFEST = "live_check_manifest.json"
# cheapest documented preset for gpt-image-2: quality low, 1536x1024 (landscape) = USD 0.005 (retrieved 2026-10-12)
DEFAULT_RESOLUTION = "1536x1024"
DEFAULT_QUALITY = "draft"  # -> OpenAI quality "low"
PROMPT = "Abstract illustration of a calm harbour at dawn, soft colours, no text, no people"
INVALID_KEY = "sk-invalid-vvm-live-check-000000000000000000000000"


def _key_info() -> tuple[bool, int, str]:
    for name in ("VIDEO_MODULE_OPENAI_API_KEY", "OPENAI_API_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return True, len(v), f"env:{name}"
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ("OPENAI_API_KEY", "VIDEO_MODULE_OPENAI_API_KEY") and v.strip().strip('"').strip("'"):
                return True, len(v.strip().strip('"').strip("'")), "module .env"
    return False, 0, "none"


def _boot(run_id: str):
    """Point the module at a throwaway storage root BEFORE importing it, then return (client, settings, run_dir)."""
    run_dir = BASE_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VIDEO_MODULE_STORAGE_ROOT"] = str(run_dir / "storage")
    os.environ.pop("VIDEO_MODULE_DATABASE_URL", None)
    os.environ["VIDEO_MODULE_API_KEY"] = ""
    os.environ["VIDEO_MODULE_GENERATION_MAX_RETRIES"] = "0"  # exactly one attempt: never re-send a billable request
    os.environ["VIDEO_MODULE_BILLABLE_GENERATION_MAX_RETRIES"] = "0"
    os.environ["VIDEO_MODULE_ALLOW_PLACEHOLDER_FALLBACK"] = "false"
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    s = get_settings()
    assert Path(s.storage_root).resolve() == (run_dir / "storage").resolve(), "storage root isolation failed"
    assert s.generation_max_retries == 0 and s.billable_generation_max_retries == 0
    return TestClient(app), s, run_dir


def _write_manifest(run_dir: Path, data: dict) -> None:
    p = run_dir / MANIFEST
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    old.update(data)
    p.write_text(json.dumps(old, indent=2, default=str), encoding="utf-8")


def _wait(client, job_id: str, timeout: float = 300.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return j
        time.sleep(0.5)
    raise SystemExit(f"job {job_id} did not finish within {timeout}s")


def _guard_transport(allow_generation_posts: int):
    """Real network transport that counts requests and refuses generation POSTs beyond the allowance."""
    import httpx

    from app.generation.openai_images import OpenAIImagesProvider

    real = httpx.HTTPTransport()
    counts = {"GET": 0, "POST_generation": 0, "refused": 0}

    class Guard(httpx.BaseTransport):
        def handle_request(self, request):
            if request.method == "POST" and request.url.path.endswith("/images/generations"):
                if counts["POST_generation"] >= allow_generation_posts:
                    counts["refused"] += 1
                    raise httpx.ConnectError("live_provider_check guard: additional billable request refused")
                counts["POST_generation"] += 1
            elif request.method == "GET":
                counts["GET"] += 1
            else:
                counts["refused"] += 1
                raise httpx.ConnectError(f"live_provider_check guard: {request.method} {request.url.path} not allowed")
            return real.handle_request(request)

    OpenAIImagesProvider._transport = Guard()
    return counts


def cmd_dry_run(a) -> int:
    present, length, src = _key_info()
    from_env = os.environ.get("VVM_ENABLE_PAID_PROVIDER_TESTS") == "1"
    print("DRY RUN - no network, nothing billed.")
    print(f"  key present: {present}  (length {length}, source {src}; the value is never shown)")
    print(f"  VVM_ENABLE_PAID_PROVIDER_TESTS=1: {from_env}")
    print(f"  model: {os.environ.get('VIDEO_MODULE_OPENAI_IMAGE_MODEL', 'gpt-image-2')}")
    print(f"  planned single generation: quality={DEFAULT_QUALITY} (OpenAI 'low'), resolution={DEFAULT_RESOLUTION}, n=1, png")
    sys.path.insert(0, str(ROOT))
    from app.generation.openai_images import QUALITY_MAP, preset_size_for
    from app.generation.openai_pricing import PRICING_RETRIEVED_ON, per_image_estimate

    model = os.environ.get("VIDEO_MODULE_OPENAI_IMAGE_MODEL", "gpt-image-2")
    est = per_image_estimate(model, QUALITY_MAP[DEFAULT_QUALITY], preset_size_for(DEFAULT_RESOLUTION))
    print(f"  documented estimate: USD {est} (image output only, pricing retrieved {PRICING_RETRIEVED_ON}); "
          f"billable generation would also need --confirm-paid and estimate <= --max-usd")
    return 0


def cmd_status(a) -> int:
    client, s, run_dir = _boot(a.run_id)
    with client:
        st = client.get("/providers/openai-images/status").json()
    print(json.dumps({"run_id": a.run_id, "state": st["state"], "model": st["model"], "billable": st["billable"],
                      "network_request_made": False}, indent=2))
    return 0


def cmd_probe(a, *, key_override: str | None = None) -> dict:
    client, s, run_dir = _boot(a.run_id)
    if key_override is not None:
        from pydantic import SecretStr

        s.openai_api_key = SecretStr(key_override)
    counts = _guard_transport(allow_generation_posts=0)
    with client:
        st = client.get("/providers/openai-images/status", params={"probe": True}).json()
    out = {"state": st["state"], "probe": st.get("probe"), "requests": counts}
    return out


def cmd_invalid_key(a) -> int:
    if a.models_only:  # only the non-billable GET /v1/models/{model}; no generation endpoint is contacted
        out = cmd_probe(a, key_override=INVALID_KEY)
        ok = out["state"] == "FAILED" and out["requests"]["POST_generation"] == 0
        print(json.dumps({"run_id": a.run_id, **out}, indent=2))
        print("PASS" if ok else "FAIL", "- invalid key rejected by the non-billable models endpoint")
        return 0 if ok else 1
    client, s, run_dir = _boot(a.run_id)
    from pydantic import SecretStr

    s.openai_api_key = SecretStr(INVALID_KEY)  # your real key is NOT used by this command
    counts = _guard_transport(allow_generation_posts=1)
    with client:
        st = client.get("/providers/openai-images/status", params={"probe": True}).json()
        pid = client.post("/projects", json={"name": f"live-check-invalid-key-{a.run_id}", "ai_image_budget_usd": 0.05}).json()["project_id"]
        j = _wait(client, client.post("/assets/generate", json={
            "project_id": pid, "prompt": PROMPT, "provider": "openai-images", "quality": DEFAULT_QUALITY,
            "resolution": DEFAULT_RESOLUTION, "allow_placeholder_fallback": False}).json()["job_id"])
        gens = client.get("/generations", params={"project_id": pid}).json()
        assets = client.get("/assets", params={"project_id": pid}).json()
    ok = (st["state"] == "FAILED" and j["status"] == "FAILED" and j["error_code"] == "PROVIDER_AUTH_FAILED"
          and len(assets) == 0 and len(gens) == 1 and gens[0]["provider_state"] == "FAILED")
    res = {"run_id": a.run_id, "probe_state": st["state"], "probe_detail": st["probe"]["detail"],
           "generation_job": {k: j.get(k) for k in ("status", "error_code")},
           "generation_record": {k: gens[0].get(k) for k in ("provider_state", "error_code", "provider_request_id")} if gens else None,
           "assets_created": len(assets), "requests": counts, "expected_behaviour": ok}
    _write_manifest(run_dir, {"run_id": a.run_id, "invalid_key_check": res})
    print(json.dumps(res, indent=2))
    print("PASS" if ok else "FAIL", "- invalid key fails safely (auth errors are not billed)")
    return 0 if ok else 1


def cmd_generate(a) -> int:
    present, length, src = _key_info()
    if not present:
        print("REFUSED: no OpenAI key configured (OPENAI_API_KEY / VIDEO_MODULE_OPENAI_API_KEY / module .env).")
        return 2
    if os.environ.get("VVM_ENABLE_PAID_PROVIDER_TESTS") != "1":
        print("REFUSED: paid testing is not enabled (set VVM_ENABLE_PAID_PROVIDER_TESTS=1 in this session).")
        return 2
    if not a.confirm_paid:
        print("REFUSED: this command makes ONE billable OpenAI request; re-run with --confirm-paid to authorise it.")
        return 2
    client, s, run_dir = _boot(a.run_id)
    with client:
        pid = client.post("/projects", json={"name": f"live-check-{a.run_id}", "ai_image_budget_usd": a.max_usd}).json()["project_id"]
        _write_manifest(run_dir, {"run_id": a.run_id, "project_id": pid})
        est = client.post("/assets/generate/estimate", json={"project_id": pid, "prompt": PROMPT, "provider": "openai-images",
                                                             "quality": DEFAULT_QUALITY, "resolution": DEFAULT_RESOLUTION}).json()
        if est.get("estimated_cost") is None or est["estimated_cost"] > a.max_usd:
            print(f"REFUSED: documented estimate {est.get('estimated_cost')} USD is unknown or above --max-usd {a.max_usd}.")
            return 2
        if est.get("provider_state") != "AVAILABLE":
            print(f"REFUSED: provider state is {est.get('provider_state')}.")
            return 2
        counts = _guard_transport(allow_generation_posts=1)
        _write_manifest(run_dir, {"run_id": a.run_id, "project_id": pid, "model": s.openai_image_model,
                                  "estimate_usd": est["estimated_cost"], "max_usd": a.max_usd, "started": time.time()})
        print(f"Sending ONE billable request (estimate USD {est['estimated_cost']}, model {s.openai_image_model}) ...")
        j = _wait(client, client.post("/assets/generate", json={
            "project_id": pid, "prompt": PROMPT, "provider": "openai-images", "quality": DEFAULT_QUALITY,
            "resolution": DEFAULT_RESOLUTION, "allow_placeholder_fallback": False}).json()["job_id"])
        gens = client.get("/generations", params={"project_id": pid}).json()
        res = {"run_id": a.run_id, "project_id": pid, "job": {k: j.get(k) for k in ("job_id", "status", "error_code", "error_message")},
               "requests": dict(counts), "generations": [{k: g.get(k) for k in ("generation_id", "provider", "model", "status", "provider_state",
                                                                                "error_code", "provider_request_id", "cost_status", "estimated_cost",
                                                                                "attempts")} for g in gens]}
        checks = []
        if j["status"] == "SUCCEEDED":
            aid = j["result_id"]
            asset = client.get(f"/assets/{aid}").json()
            raw = client.get(f"/assets/{aid}/file").content
            file_path = Path(s.storage_root) / asset["local_path"]
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as im:
                im.load()
                fmt, size = im.format, im.size
            costs = client.get(f"/costs/{pid}").json()
            meta = asset.get("extra_metadata") or {}
            checks = [
                ("exactly one generation POST sent", counts["POST_generation"] == 1 and counts["refused"] == 0),
                ("generation_kind AI_GENERATED / provenance AI", asset["generation_kind"] == "AI_GENERATED" and asset["is_real_ai"] is True
                 and asset["is_placeholder"] is False and asset["is_fallback"] is False),
                ("not evidence", asset["evidence_eligible"] is False),
                ("decoded format matches mime", (fmt, asset["mime_type"]) in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"))),
                ("decoded size == recorded size", list(size) == [asset["width"], asset["height"]]),
                ("size is the requested preset", f"{size[0]}x{size[1]}" == meta.get("requested_size")),
                ("file on disk exists inside run storage", file_path.is_file() and Path(s.storage_root).resolve() in file_path.resolve().parents),
                ("sha256(file) == checksum_sha256", hashlib.sha256(file_path.read_bytes()).hexdigest() == asset["checksum_sha256"]
                 == hashlib.sha256(raw).hexdigest()),
                ("provider/model/provider_request_id recorded", asset["provider"] == "openai-images" and bool(asset["model"])
                 and bool(asset["provider_request_id"])),
                ("provider_created recorded", bool(meta.get("provider_created"))),
                ("cost status KNOWN with amount, or UNKNOWN with null",
                 (asset["cost_status"] == "KNOWN" and isinstance(asset["generation_cost"], (int, float)))
                 or (asset["cost_status"] == "UNKNOWN" and asset["generation_cost"] is None)),
                ("known cost within --max-usd (if KNOWN)", asset["cost_status"] != "KNOWN" or asset["generation_cost"] <= a.max_usd),
                ("cost report consistent", costs["ai_image_count"] == 1 and costs["known_cost_count"] + costs["unknown_cost_count"] == 1),
            ]
            res["asset"] = {k: asset.get(k) for k in ("asset_id", "generation_kind", "provenance_class", "asset_type", "source_type", "mime_type",
                                                      "width", "height", "provider", "model", "provider_request_id", "cost_status",
                                                      "generation_cost", "checksum_sha256", "local_path", "title")}
            res["asset"]["usage"] = meta.get("usage")
            res["asset"]["provider_created"] = meta.get("provider_created")
            res["asset"]["c2pa_manifest_present"] = meta.get("c2pa_manifest_present")
            res["cost_report"] = {k: costs.get(k) for k in ("ai_image_count", "ai_generation_cost", "known_cost_count", "unknown_cost_count",
                                                            "totals_complete")}
        res["checks"] = [{"check": n, "pass": bool(ok)} for n, ok in checks]
        _write_manifest(run_dir, {"generate_result": res})
    print(json.dumps(res, indent=2, default=str))
    if j["status"] != "SUCCEEDED":
        print(f"GENERATION FAILED: {j.get('error_code')} - see generations[].error_code / provider_state above. "
              "Do NOT retry automatically; read the runbook troubleshooting section first.")
        return 1
    bad = [n for n, ok in checks if not ok]
    print("ALL CHECKS PASS" if not bad else f"FAILED CHECKS: {bad}")
    print(f"Run id for cleanup: {a.run_id}")
    return 0 if not bad else 1


def cmd_cleanup(a) -> int:
    run_dir = (BASE_DIR / a.run_id).resolve()
    if BASE_DIR.resolve() not in run_dir.parents or not run_dir.is_dir():
        print(f"Nothing to clean: {run_dir} is not a live-check run directory.")
        return 1
    m = run_dir / MANIFEST
    if m.exists() and json.loads(m.read_text(encoding="utf-8")).get("run_id") not in (None, a.run_id):
        print("REFUSED: manifest run_id does not match.")
        return 1
    shutil.rmtree(run_dir)
    print(f"Removed {run_dir} (only this run's throwaway storage; the module's storage/ was never used).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", default="dry-run",
                   choices=["dry-run", "status", "probe", "invalid-key", "generate", "cleanup"])
    p.add_argument("--run-id", default=None, help="run directory id (default: new random id; required for cleanup)")
    p.add_argument("--confirm-paid", action="store_true", help="authorise the ONE billable request of 'generate'")
    p.add_argument("--max-usd", type=float, default=0.01, help="refuse 'generate' if the documented estimate exceeds this")
    p.add_argument("--models-only", action="store_true", help="invalid-key: only the GET /models probe, no generation endpoint")
    a = p.parse_args()
    if a.command == "cleanup" and not a.run_id:
        p.error("cleanup requires --run-id")
    a.run_id = a.run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    if a.command == "dry-run":
        return cmd_dry_run(a)
    if a.command == "status":
        return cmd_status(a)
    if a.command == "probe":
        if not _key_info()[0]:
            print("REFUSED: no key configured; probe needs a key (it is non-billable).")
            return 2
        out = cmd_probe(a)
        print(json.dumps({"run_id": a.run_id, **out}, indent=2))
        ok = out["state"] == "AVAILABLE" and out["requests"]["POST_generation"] == 0
        print("PASS" if ok else "FAIL", "- non-billable models probe (no generation request was sent)")
        return 0 if ok else 1
    if a.command == "invalid-key":
        return cmd_invalid_key(a)
    if a.command == "generate":
        return cmd_generate(a)
    return cmd_cleanup(a)


if __name__ == "__main__":
    sys.exit(main())
