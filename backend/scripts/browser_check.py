"""Rendered-browser check of the Production tab against a populated job.

Starts the core app on a throw-away port with the dev-auth bypass for the job's
owner, drives real Chrome over the DevTools protocol, and records for each
viewport: JavaScript exceptions, horizontal overflow, section presence and a
screenshot. Usage: browser_check.py <db.sqlite3> <storage_root> <job_id> <owner> <out_dir>
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
VIEWPORTS = [(390, 844, "mobile"), (768, 1024, "tablet"), (1440, 900, "desktop")]


async def cdp_run(ws_url: str, base: str, token: str, job: str, out: Path) -> list[dict]:
    results = []
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        mid = 0
        errors: list[str] = []

        async def call(method, params=None):
            nonlocal mid
            mid += 1
            my = mid
            await ws.send(json.dumps({"id": my, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("method") == "Runtime.exceptionThrown":
                    errors.append(msg["params"]["exceptionDetails"].get("text", "exception"))
                if msg.get("id") == my:
                    return msg.get("result", {})

        await call("Runtime.enable")
        await call("Page.enable")
        await call("Page.navigate", {"url": base + "/"})
        await asyncio.sleep(1.5)
        await call("Runtime.evaluate", {"expression": (
            f"localStorage.setItem('ds_review_token', {json.dumps(token)});"
            f"localStorage.setItem('ds_review_job_id', {json.dumps(job)}); true")})
        for width, height, label in VIEWPORTS:
            errors.clear()
            await call("Emulation.setDeviceMetricsOverride",
                       {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600})
            await call("Page.navigate", {"url": base + "/#production"})
            await asyncio.sleep(0.5)
            await call("Page.reload")
            await asyncio.sleep(4.0)  # sections load several API calls
            info = await call("Runtime.evaluate", {"returnByValue": True, "expression": """(() => {
              const secs=[...document.querySelectorAll('.prod-section')].map(s=>s.dataset.section);
              const errs=[...document.querySelectorAll('.error-state')].map(e=>e.textContent.slice(0,120));
              return {sections:secs, errors:errs, scrollW:document.documentElement.scrollWidth,
                      clientW:document.documentElement.clientWidth, text:document.body.innerText.length,
                      wide:[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>document.documentElement.clientWidth+1).slice(0,6).map(e=>e.tagName+'.'+e.className+'#'+(e.id||'')+' '+Math.round(e.getBoundingClientRect().right))};})()"""})
            val = info["result"]["value"]
            shot = await call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            path = out / f"production-{label}-{width}.png"
            path.write_bytes(base64.b64decode(shot["data"]))
            results.append({"viewport": f"{width}x{height}", "sections": val["sections"], "section_errors": val["errors"],
                            "horizontal_overflow": val["scrollW"] > val["clientW"] + 1, "wide_elements": val["wide"],
                            "js_exceptions": list(errors), "screenshot": str(path), "body_text_chars": val["text"]})
    return results


def main() -> int:
    db, store, job, owner, out = sys.argv[1:6]
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    port = 8731
    env = {**os.environ, "DEFENCE_STUDIO_DB_PATH": db, "DEFENCE_STUDIO_STORAGE_PATH": store,
           "DEFENCE_STUDIO_AUTH_SECRET": "phase-one-test-signing-secret-32-bytes-minimum", "DEFENCE_STUDIO_DEV_MODE": "1",
           "DEFENCE_STUDIO_DEV_AUTH_BYPASS": "1", "DEFENCE_STUDIO_DEV_AUTH_OWNER": owner, "PYTHONDONTWRITEBYTECODE": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "defence_studio.api.app:app", "--app-dir",
                               str(Path(__file__).resolve().parents[1]), "--port", str(port)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/dev/session", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        token = json.loads(urllib.request.urlopen(base + "/api/dev/session", timeout=5).read())["token"]
        profile = out_dir / "chrome-profile"
        chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                                   "--remote-debugging-port=9333", f"--user-data-dir={profile}", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ws_url = None
            for _ in range(40):
                try:
                    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=1).read())
                    ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                    break
                except Exception:
                    time.sleep(0.5)
            assert ws_url, "Chrome DevTools endpoint did not start"
            results = asyncio.run(cdp_run(ws_url, base, token, job, out_dir))
        finally:
            chrome.terminate()
        (out_dir / "browser-check.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        for r in results:
            print(r["viewport"], "sections:", len(r["sections"]), "overflow:", r["horizontal_overflow"],
                  "js_exceptions:", len(r["js_exceptions"]), "section_errors:", len(r["section_errors"]))
        return 0
    finally:
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
