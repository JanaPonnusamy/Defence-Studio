"""
AUTOMATED browser verification of the module UI (Phase 2 / G). Not a human test.

Drives the real UI in headless Microsoft Edge (Playwright, channel="msedge"; no browser
download) against a live server started by this script on a free port with ISOLATED temp
storage and NO provider credentials. Saves screenshots + results.json to
docs/ui_screenshots/. Exit code 0 = every check passed and no unexpected console errors.

    .venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt
    .venv\\Scripts\\python.exe scripts\\ui_browser_check.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "ui_screenshots"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    from playwright.sync_api import sync_playwright

    from fixtures.synthetic import png_bytes, real_mp4_bytes, real_pdf_bytes, truncated_png

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    tmp = Path(tempfile.mkdtemp(prefix="vvm_ui_"))
    files = tmp / "files"
    files.mkdir()
    (files / "harbour_photo.png").write_bytes(png_bytes(960, 540, 77, text="Kestrel harbour"))
    (files / "corrupt.png").write_bytes(truncated_png(5))
    (files / "memo.pdf").write_bytes(real_pdf_bytes(2))
    mp4 = real_mp4_bytes(2.0)
    if mp4:
        (files / "patrol.mp4").write_bytes(mp4)

    port = free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "VIDEO_MODULE_OPENAI_API_KEY",
                                                            "VIDEO_MODULE_ALLOW_PLACEHOLDER_FALLBACK", "PYTHONPATH")}
    env["VIDEO_MODULE_STORAGE_ROOT"] = str(tmp / "storage")
    env["VIDEO_MODULE_API_KEY"] = ""
    # Audit fix: EMPTY key vars override any key in the module .env (removing them would let .env supply one,
    # and this check submits openai-images requests).
    env["OPENAI_API_KEY"] = ""
    env["VIDEO_MODULE_OPENAI_API_KEY"] = ""
    server =subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    checks: list[dict] = []
    console: list[dict] = []
    bad_requests: list[dict] = []
    shots: list[str] = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": str(detail)[:300]})
        print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))

    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = ctx.new_page()
            page.on("console", lambda m: console.append({"type": m.type, "text": m.text}) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append({"type": "pageerror", "text": str(e)}))
            page.on("response", lambda r: bad_requests.append({"status": r.status, "url": r.url.replace(base, "")})
                    if r.status >= 400 else None)

            def snap(name):
                page.wait_for_timeout(250)
                path = OUT / f"{len(shots) + 1:02d}_{name}.png"
                page.screenshot(path=str(path), full_page=True)
                shots.append(path.name)

            def wait_detail(title):
                page.wait_for_function("t => { const d = document.querySelector('#assetDetail'); const i = d.querySelector('#detailImg');"
                                       " return d.textContent.includes(t) && i && i.naturalWidth > 0; }", arg=title, timeout=15000)

            def tab(name):
                page.click(f"button.tab[data-tab='{name}']")
                page.wait_for_timeout(300)

            def toast_text(expect: str = ""):
                """Wait for the toast to show `expect` (the previous toast can still be visible)."""
                try:
                    page.wait_for_function("e => { const t = document.querySelector('#toast'); "
                                           "return !t.classList.contains('hidden') && t.textContent.includes(e); }",
                                           arg=expect, timeout=15000)
                except Exception:  # noqa: BLE001 -- report what is actually shown
                    pass
                return page.inner_text("#toast")

            # 1. empty state
            page.goto(base + "/ui/")
            page.wait_for_function("document.querySelector('#apiStatus').textContent.includes('API ok')", timeout=10000)
            check("api status shown", "v1.1.0" in page.inner_text("#apiStatus"), page.inner_text("#apiStatus"))
            check("empty state: no project", "Select or create a project" in page.inner_text("#projectSummary"))
            snap("empty_state_dashboard")

            # 2. project creation
            page.fill("#newProjectForm input[name=name]", "UI check project")
            page.fill("#newProjectForm input[name=ai_image_budget_usd]", "2")
            page.click("#newProjectForm button[type=submit]")
            check("project created toast", "Project created" in toast_text("Project created"))
            page.wait_for_function("document.querySelector('#projectSummary').textContent.includes('UI check project')")
            snap("project_created")

            # 3. assets: empty, import image, error state, video/pdf previews
            tab("assets")
            page.wait_for_function("document.querySelector('#assetsTable tbody').textContent.includes('No assets')")
            check("assets empty state", True)
            f = page.locator("#importForm")
            f.locator("input[name=file]").set_input_files(str(files / "harbour_photo.png"))
            f.locator("select[name=asset_type]").select_option("SOURCE_PHOTO")
            f.locator("select[name=source_type]").select_option("EXTERNAL_SOURCE")
            f.locator("select[name=rights_status]").select_option("LICENSED")
            f.locator("input[name=title]").fill("Kestrel harbour fleet photo")
            f.locator("input[name=licence]").fill("Agency licence 42")
            f.locator("input[name=attribution]").fill("Photo: Example Agency")
            f.locator("input[name=tags]").fill("kestrel, harbour, fleet, warships, coast")
            f.locator("input[name=source_identity]").fill("Example Agency / 42")
            f.locator("input[name=attribution_required]").check()
            f.locator("button[type=submit]").click()
            check("image import toast", "Imported SOURCE_PHOTO" in toast_text("Imported SOURCE_PHOTO"))
            page.wait_for_selector("#assetDetail img#detailImg[src]", timeout=8000)
            det = page.inner_text("#assetDetail")
            check("asset detail shows provenance", "ORIGINAL_SOURCE" in det and "attribution (REQUIRED)" in det, det[:200])
            snap("asset_imported_detail")
            f.locator("input[name=file]").set_input_files(str(files / "corrupt.png"))
            f.locator("select[name=asset_type]").select_option("IMAGE")
            f.locator("input[name=title]").fill("corrupt")
            f.locator("button[type=submit]").click()
            t = toast_text("ASSET_INVALID")
            check("error state: corrupt import rejected", "ASSET_INVALID" in t, t)
            page.wait_for_function("document.querySelector('#toast').classList.contains('err')")
            snap("error_corrupt_import")
            f.locator("input[name=file]").set_input_files(str(files / "memo.pdf"))
            f.locator("select[name=asset_type]").select_option("DOCUMENT")
            f.locator("select[name=source_type]").select_option("USER_PROVIDED")
            f.locator("select[name=rights_status]").select_option("PERMITTED")
            f.locator("input[name=title]").fill("Leaked order memo document")
            f.locator("input[name=attribution_required]").uncheck()
            f.locator("input[name=tags]").fill("leaked, document, order")
            f.locator("button[type=submit]").click()
            check("pdf import", "Imported DOCUMENT" in toast_text("Imported DOCUMENT"))
            wait_detail("Leaked order memo document")
            check("pdf page preview rendered", page.evaluate("document.querySelector('#detailImg').naturalWidth") > 0)
            snap("pdf_page_preview")
            if mp4:
                f.locator("input[name=file]").set_input_files(str(files / "patrol.mp4"))
                f.locator("select[name=asset_type]").select_option("VIDEO_CLIP")
                f.locator("input[name=title]").fill("Patrol boat clip")
                f.locator("input[name=duration]").fill("")
                f.locator("button[type=submit]").click()
                check("video import without duration (probed)", "Imported VIDEO_CLIP" in toast_text("Imported VIDEO_CLIP"))
                wait_detail("Patrol boat clip")
                check("video frame preview rendered", page.evaluate("document.querySelector('#detailImg').naturalWidth") > 0)
                check("probed duration shown", "2.00s" in page.inner_text("#assetDetail"), page.inner_text("#assetDetail")[:200])
                snap("video_frame_preview")
            page.click("#refreshAssets")
            page.wait_for_timeout(800)
            rows = page.locator("#assetsTable tbody tr").count()
            check("library lists imported assets", rows == (3 if mp4 else 2), rows)
            snap("asset_library")

            # 4. maps & stats
            tab("graphics")
            page.fill("#statForm input[name=title]", "Warships deployed")  # required field (HTML5 validation)
            page.click("#statForm button[type=submit]")
            check("stat graphic rendered", "Statistic graphic rendered" in toast_text("Statistic graphic rendered"))
            page.fill("#mapForm input[name=title]", "Black Sea ports")
            page.fill("#mapForm input[name=data_source]", "Synthetic UI-test coordinates")
            page.check("#mapForm input[name=basemap]")
            page.click("#mapForm button[type=submit]")
            check("map rendered", "Map rendered" in toast_text("Map rendered"))
            page.wait_for_timeout(500)
            check("basemap fallback disclosed", "schematic fallback" in page.inner_text("#graphicResult"), page.inner_text("#graphicResult"))
            snap("map_schematic_fallback")

            # 5. generation: provider states, not-configured error, explicit fallback, placeholder
            tab("generation")
            opts = page.inner_text("#genForm select[name=provider]")
            check("provider list honest", "PLACEHOLDER - not AI" in opts and "NOT CONFIGURED" in opts, opts)
            page.select_option("#genForm select[name=provider]", "openai-images")
            page.click("#checkProviderBtn")
            page.wait_for_function("document.querySelector('#estimateBox').textContent.includes('NOT_CONFIGURED')")
            check("provider check shows NOT_CONFIGURED", True)
            page.click("#genForm button[type=submit]")
            page.wait_for_function("document.querySelector('#genStatus').textContent.includes('PROVIDER_NOT_CONFIGURED')")
            check("error state: real AI not configured, no silent placeholder", True)
            snap("generation_not_configured_error")
            page.check("#genForm input[name=allow_placeholder_fallback]")
            page.select_option("#genForm select[name=resolution]", "640x360")
            page.click("#genForm button[type=submit]")
            page.wait_for_selector("#genStatus img#genImg[src]", timeout=20000)
            st = page.inner_text("#genStatus")
            check("explicit fallback labelled", "FALLBACK for openai-images" in st and "PLACEHOLDER" in st and "NOT_APPLICABLE" in st, st)
            snap("generation_explicit_fallback")
            page.uncheck("#genForm input[name=allow_placeholder_fallback]")
            page.select_option("#genForm select[name=provider]", "placeholder-procedural")
            page.click("#estimateBtn")
            page.wait_for_function("document.querySelector('#estimateBox').textContent.includes('not applicable')")
            check("placeholder estimate: cost not applicable", True)

            # 6. planner: scenes, build (loading -> completed), asset assignment, failed submission
            tab("planner")
            page.fill("#scenesForm textarea[name=narration]",
                      "The fleet gathered in Kestrel harbour at dawn. Warships lined the coast.\n\n"
                      "According to a leaked document, the order was signed. Strikes escalated overnight.")
            page.click("#scenesForm button[type=submit]")
            check("scenes created", "scene(s) created" in toast_text("scene(s) created"))
            page.wait_for_timeout(500)
            check("scenes table populated", page.locator("#scenesTable tbody tr").count() >= 2)
            page.select_option("#buildForm select[name=ai_provider]", "openai-images")
            page.check("#buildForm input[name=generate_ai]")
            page.click("#buildForm button[type=submit]")
            page.wait_for_function("document.querySelector('#buildStatus').textContent.includes('PROVIDER_NOT_CONFIGURED')")
            check("error state: plan with unconfigured AI refused", True)
            snap("plan_build_refused")
            page.select_option("#buildForm select[name=ai_provider]", "placeholder-procedural")
            page.fill("#buildForm input[name=output]", "1920x1080@30")
            page.click("#buildForm button[type=submit]")
            page.wait_for_function("document.querySelector('#buildStatus').textContent.match(/RUNNING|QUEUED|SUCCEEDED|Submitting/)")
            check("loading state shown during build", True, page.inner_text("#buildStatus"))
            page.wait_for_function("document.querySelector('#planSummary').textContent.includes('BUILT')", timeout=30000)
            summ = page.inner_text("#planSummary")
            check("plan summary honest counts", "real AI images 0" in summ and "placeholders" in summ, summ[:300])
            n_shots = page.locator("#shotsTable tbody tr").count()
            ready = page.locator("#shotsTable tbody tr:has-text('READY')").count()
            check("shots listed with assigned assets", n_shots > 0 and ready > 0, f"{ready}/{n_shots}")
            check("timeline bar segments", page.locator("#timelineBar .seg").count() == n_shots)
            snap("plan_built")

            # 7. previews
            tab("preview")
            page.wait_for_function("document.querySelector('#pvStart').naturalWidth > 0", timeout=15000)
            check("shot preview frames load", True)
            snap("shot_preview")
            page.click("#loadSheet")
            page.wait_for_function("document.querySelector('#contactSheet').naturalWidth > 0", timeout=20000)
            check("contact sheet renders", True)
            snap("contact_sheet")

            # 8. costs
            tab("costs")
            page.wait_for_function("document.querySelector('#costSummary').textContent.includes('PLACEHOLDER images')")
            cs = page.inner_text("#costSummary")
            check("costs: placeholders not AI, no invented cost", "REAL AI images" in cs and "$0" in cs, cs[:300])
            check("costs: records show NOT_APPLICABLE", "NOT_APPLICABLE" in page.inner_text("#costRecords"))
            snap("costs")

            # 9. persistence across refresh
            page.reload()
            page.wait_for_function("document.querySelector('#costSummary').textContent.includes('PLACEHOLDER images')", timeout=15000)
            check("refresh keeps project and tab", page.eval_on_selector("button.tab.active", "b => b.dataset.tab") == "costs"
                  and page.input_value("#projectSelect") != "")
            tab("planner")
            page.wait_for_function("document.querySelector('#planSummary').textContent.includes('BUILT')", timeout=15000)
            check("refresh: built plan persisted", page.locator("#shotsTable tbody tr").count() == n_shots)
            tab("assets")
            page.wait_for_timeout(800)
            check("refresh: assets persisted", page.locator("#assetsTable tbody tr").count() >= (3 if mp4 else 2))
            snap("after_refresh_assets")

            # 10. diagnostics
            tab("diagnostics")
            page.wait_for_function("document.querySelector('#diagBox').textContent.includes('previews')")
            check("diagnostics shows preview capabilities", "video_frames" in page.inner_text("#diagBox"))
            snap("diagnostics")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(10)
        except Exception:  # noqa: BLE001
            server.kill()

    expected_status = {(409, "/assets/generate"), (409, "/visual-plans/build"), (422, "/assets/import")}
    unexpected_requests = [r for r in bad_requests if (r["status"], r["url"].split("?")[0]) not in expected_status]
    # the browser logs every 4xx as "Failed to load resource: ... status of N"; one such message per deliberate
    # error-state request (matched by status code) is expected, anything else is a defect
    budget: dict[int, int] = {}
    for r in bad_requests:
        if r not in unexpected_requests:
            budget[r["status"]] = budget.get(r["status"], 0) + 1
    unexpected_console = []
    for c in console:
        m = __import__("re").search(r"status of (\d{3})", c["text"]) if "Failed to load resource" in c["text"] else None
        if m and budget.get(int(m.group(1)), 0) > 0:
            budget[int(m.group(1))] -= 1
        else:
            unexpected_console.append(c)
    check("no unexpected console errors", not unexpected_console, unexpected_console)
    check("no unexpected failed requests", not unexpected_requests, unexpected_requests)
    result = {"kind": "AUTOMATED headless Microsoft Edge (Playwright channel=msedge); not human interaction",
              "base_url": "isolated temp server", "checks": checks, "passed": sum(c["pass"] for c in checks),
              "failed": sum(not c["pass"] for c in checks), "screenshots": shots,
              "console_messages": console, "http_errors": bad_requests, "unexpected_http_errors": unexpected_requests,
              "unexpected_console": unexpected_console, "video_fixture_available": bool(mp4)}
    (OUT / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nUI CHECKS: {result['passed']} passed, {result['failed']} failed; screenshots: {len(shots)} in {OUT}")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
