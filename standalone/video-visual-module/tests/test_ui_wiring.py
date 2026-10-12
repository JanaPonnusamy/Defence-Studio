"""UI wiring: every element the JS drives exists, every button is handled, every API path the UI calls exists."""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"


def test_js_element_ids_exist_in_html():
    html = (UI / "index.html").read_text(encoding="utf-8")
    js = (UI / "app.js").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)', js)) | set(re.findall(r'\$\(`#([A-Za-z0-9_-]+)', js))
    dynamic = {"detailImg", "variantForm", "gImg", "genImg"}  # created by JS itself
    missing = sorted(js_ids - html_ids - dynamic)
    assert missing == [], missing


def test_every_button_does_something():
    html = (UI / "index.html").read_text(encoding="utf-8")
    js = (UI / "app.js").read_text(encoding="utf-8")
    for m in re.finditer(r"<button([^>]*)>", html):
        attrs = m.group(1)
        if "data-tab" in attrs or 'type="submit"' in attrs:
            continue
        bid = re.search(r'id="([^"]+)"', attrs)
        assert bid, f"button without id/handler: {attrs}"
        assert f'"#{bid.group(1)}"' in js and "addEventListener" in js, f"no handler for #{bid.group(1)}"
    for form_id in re.findall(r'<form id="([^"]+)"', html):
        assert re.search(rf'\$\("#{form_id}"\)\.addEventListener\("submit"', js), f"form {form_id} has no submit handler"


def test_ui_api_paths_exist(client):
    js = (UI / "app.js").read_text(encoding="utf-8")
    paths = set(client.get("/openapi.json").json()["paths"])
    called = set(re.findall(r'["`](/(?:projects|assets|scenes|visual-plans|shots|jobs|costs|providers|diagnostics|health)[^"`?]*)', js))
    norm = lambda p: re.sub(r"\$\{[^}]+\}", "{x}", p)  # noqa: E731
    spec = {re.sub(r"\{[^}]+\}", "{x}", p) for p in paths}
    assert len(called) >= 15, called
    unknown = sorted(p for p in called if norm(p) not in spec)
    assert unknown == [], unknown
