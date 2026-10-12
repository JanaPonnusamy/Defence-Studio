"""Loads the synthetic 10-minute episode into a project through the PUBLIC API."""
from __future__ import annotations

import json

from fixtures.synthetic import pdf_bytes, png_bytes, ten_minute_episode


def load_episode(client, project_id: str) -> dict:
    narration, specs = ten_minute_episode()
    counts: dict[str, int] = {}
    for spec in specs:
        kind = spec["kind"]
        if kind in ("photo", "satellite"):
            r = client.post("/assets/import", data={
                "project_id": project_id, "asset_type": "SOURCE_PHOTO" if kind == "photo" else "SATELLITE",
                "source_type": "EXTERNAL_SOURCE", "rights_status": spec["rights_status"], "licence": spec["licence"],
                "title": spec["title"], "attribution": f"Synthetic fixture ({kind})",
                "extra_metadata": json.dumps({"tags": spec["tags"],
                                              "protected_regions": [{"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.12, "label": "markings"}]}),
            }, files={"file": (f"{kind}_{spec['seed']}.png", png_bytes(1280, 720, spec["seed"]), "image/png")})
        elif kind == "document":
            r = client.post("/assets/import", data={
                "project_id": project_id, "asset_type": "DOCUMENT", "source_type": "EXTERNAL_SOURCE",
                "rights_status": spec["rights_status"], "licence": spec["licence"], "title": spec["title"],
                "attribution": "Synthetic official release",
                "extra_metadata": json.dumps({"tags": spec["tags"], "regions": [
                    {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.25, "label": "order heading", "page": 1},
                    {"x": 0.1, "y": 0.55, "w": 0.6, "h": 0.2, "label": "signature block", "page": 1}]}),
            }, files={"file": (f"doc_{spec['seed']}.png", png_bytes(1240, 1754, spec["seed"]), "image/png")})
        elif kind == "map":
            r = client.post("/assets/maps", json={"project_id": project_id, "tags": spec["tags"], "spec": {
                "title": spec["title"], "data_source": "Synthetic fixture coordinates",
                "markers": [{"label": spec["label"], "lat": spec["lat"], "lon": spec["lon"]},
                            {"label": "Staging point", "lat": spec["lat"] + 0.4, "lon": spec["lon"] - 0.6}],
                "route": [{"lat": spec["lat"] + 0.4, "lon": spec["lon"] - 0.6}, {"lat": spec["lat"], "lon": spec["lon"]}]}})
        elif kind == "stat":
            r = client.post("/assets/stats", json={"project_id": project_id, "tags": spec["tags"], "spec": {
                "graphic_type": "STAT_CARD", "title": spec["title"], "data": [
                    {"label": spec["title"], "value": spec["value"], "unit": spec["unit"], "source": "Synthetic ministry release",
                     "as_of": "2026-09-30", "confidence": "OFFICIAL"}]}})
        elif kind == "chart":
            r = client.post("/assets/stats", json={"project_id": project_id, "tags": spec["tags"], "spec": {
                "graphic_type": "NUMBER_CALLOUT", "title": spec["title"], "data": [
                    {"label": "change vs last year", "value": spec["value"], "unit": "percent",
                     "source": "Synthetic analyst estimate", "as_of": "2026-09-30", "confidence": "ESTIMATE"}]}})
        else:  # pragma: no cover
            raise ValueError(kind)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"fixture import failed for {kind}: {r.status_code} {r.text[:300]}")
        counts[kind] = counts.get(kind, 0) + 1
    _ = pdf_bytes  # documents are page images here; PDF import is covered by asset tests
    return {"narration": narration, "asset_counts": counts, "asset_total": sum(counts.values())}
