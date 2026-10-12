"""Phase 2 / C: provenance and rights. Restricted assets are never selected; unknown rights never get top
(evidence) priority because of relevance; permitted assets are selected; provenance is tracked."""
from __future__ import annotations

import io
import json

from PIL import Image

from app.config import get_settings
from fixtures.synthetic import png_bytes
from tests.conftest import import_png, wait_job

NARR = "The Talon missile battery was photographed at the Kestrel coastal base."


def _plan(client, project, **opts):
    client.post("/scenes", json={"project_id": project, "narration": NARR, "replace_existing": True})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project, "options": opts}).json()["job_id"])
    assert done["status"] == "SUCCEEDED", done
    return client.get(f"/shots/{done['result_id']}").json()["shots"]


def _ids(shots):
    return {s["asset_id"] for s in shots if s["asset_id"]}


def test_restricted_never_selected_even_when_most_relevant(client, project):
    tags = {"tags": ["talon", "missile", "battery", "kestrel", "coastal", "base"]}
    r = import_png(client, project, seed=5101, title="Talon missile battery Kestrel coastal base", rights_status="RESTRICTED",
                   licence=None, meta=tags).json()
    for opts in ({}, {"allow_unknown_rights": True}):
        shots = _plan(client, project, **opts)
        assert r["asset_id"] not in _ids(shots)
        assert all(s["status"] == "GAP" for s in shots)


def test_unknown_rights_never_outrank_permitted_assets(client, project):
    # the UNKNOWN asset is far more relevant (every keyword); the PERMITTED one matches one word
    unk = import_png(client, project, seed=5201, title="Talon missile battery Kestrel coastal base photographed",
                     source_type="EXTERNAL_SOURCE", rights_status="UNKNOWN", licence=None,
                     meta={"tags": ["talon", "missile", "battery", "kestrel", "coastal", "base"]}).json()
    ok = import_png(client, project, seed=5202, title="missile", source_type="USER_PROVIDED", rights_status="PERMITTED",
                    licence=None, meta={"tags": ["missile"]}).json()
    shots = _plan(client, project, allow_unknown_rights=True)
    used = [s for s in shots if s["asset_id"]]
    assert used and used[0]["asset_id"] == ok["asset_id"]
    assert unk["asset_id"] not in _ids(shots)
    a = client.get(f"/assets/{unk['asset_id']}").json()
    assert a["evidence_eligible"] is False and a["rights_usable"] is False


def test_unknown_rights_only_used_when_allowed_and_flagged(client, project):
    unk = import_png(client, project, seed=5301, title="Talon missile battery", source_type="EXTERNAL_SOURCE",
                     rights_status="UNKNOWN", licence=None, meta={"tags": ["talon", "missile", "battery"]}).json()
    shots = _plan(client, project)  # default: unknown rights not allowed
    assert unk["asset_id"] not in _ids(shots) and any(s["gap_code"] == "RIGHTS_UNKNOWN" for s in shots)
    shots = _plan(client, project, allow_unknown_rights=True)
    s = next(s for s in shots if s["asset_id"] == unk["asset_id"])
    assert "UNKNOWN rights" in s["editorial_reason"] and "NOT evidence-eligible" in s["editorial_reason"]
    assert any(o["type"] == "rights_warning" for o in s["overlays"])


def test_permitted_asset_selected_with_full_provenance(client, project):
    data = {"project_id": project, "asset_type": "SOURCE_PHOTO", "source_type": "EXTERNAL_SOURCE",
            "rights_status": "LICENSED", "licence": "Agency licence #42", "title": "Talon missile battery",
            "attribution": "Photo: Example Agency", "attribution_required": "true", "creator": "J. Doe",
            "source_url": "https://example.org/photo/1", "source_identity": "Example Agency / record 1",
            "licence_url": "https://example.org/licence", "usage_rights": "Editorial use only",
            "source_retrieved_at": "2026-10-01T10:00:00Z", "extra_metadata": json.dumps({"tags": ["talon", "missile"]})}
    a = client.post("/assets/import", data=data, files={"file": ("p.png", png_bytes(640, 360, 5401), "image/png")}).json()
    assert a["provenance_class"] == "ORIGINAL_SOURCE" and a["evidence_eligible"] is True and a["rights_usable"] is True
    assert a["attribution_required"] is True and a["source_identity"] == "Example Agency / record 1"
    assert a["source_retrieved_at"].startswith("2026-10-01") and a["acquired_at"] and len(a["checksum_sha256"]) == 64
    assert a["perceptual_hash"] and a["generation_kind"] == "NOT_GENERATED"
    side = json.loads((get_settings().source_metadata_dir / f"{a['asset_id']}.json").read_text(encoding="utf-8"))
    assert side["usage_rights"] == "Editorial use only" and side["provenance_class"] == "ORIGINAL_SOURCE"
    shots = _plan(client, project)
    assert a["asset_id"] in _ids(shots)
    done_plan = client.get("/visual-plans", params={"project_id": project}).json()[0]["visual_plan_id"]
    tl = client.get(f"/visual-plans/{done_plan}/timeline").json()
    ref = tl["assets"][a["asset_id"]]
    assert ref["evidence_eligible"] is True and ref["attribution_required"] is True and ref["source_identity"]
    att = next(x for x in tl["attributions"] if x["asset_id"] == a["asset_id"])
    assert att["attribution_required"] is True and att["attribution"] == "Photo: Example Agency"


def test_provenance_validation_rules(client, project):
    base = {"project_id": project, "asset_type": "IMAGE", "source_type": "USER_PROVIDED", "rights_status": "PERMITTED"}

    def imp(extra, seed):
        return client.post("/assets/import", data={**base, **extra}, files={"file": ("x.png", png_bytes(64, 64, seed), "image/png")})
    r = imp({"attribution_required": "true"}, 5501)
    assert r.status_code == 422 and r.json()["error_code"] == "RIGHTS_UNKNOWN"
    assert imp({"source_retrieved_at": "2999-01-01T00:00:00Z"}, 5502).status_code == 422
    assert imp({"source_retrieved_at": "yesterday"}, 5503).status_code == 422
    r = imp({"licence_url": "javascript:alert(1)"}, 5504)
    assert r.status_code == 422 and r.json()["error_code"] == "INVALID_URL"


def test_exact_and_near_duplicate_detection(client, project):
    raw = png_bytes(640, 360, 5601)
    first = import_png(client, project, seed=5601, title="orig").json()
    again = client.post("/assets/import", data={"project_id": project, "asset_type": "SOURCE_PHOTO",
                                                "source_type": "EXTERNAL_SOURCE", "rights_status": "LICENSED",
                                                "licence": "L"}, files={"file": ("i.png", raw, "image/png")})
    assert again.status_code == 200 and again.json()["deduplicated"] is True
    # same picture re-encoded as JPEG: different bytes/sha256, same perceptual hash -> flagged near-duplicate
    buf = io.BytesIO()
    Image.open(io.BytesIO(raw)).convert("RGB").save(buf, format="JPEG", quality=85)
    near = client.post("/assets/import", data={"project_id": project, "asset_type": "SOURCE_PHOTO",
                                               "source_type": "EXTERNAL_SOURCE", "rights_status": "LICENSED", "licence": "L"},
                       files={"file": ("i.jpg", buf.getvalue(), "image/jpeg")}).json()
    assert near["checksum_sha256"] != first["checksum_sha256"] and near["duplicate_of"] == first["asset_id"]
    other = import_png(client, project, seed=5699, title="different").json()
    assert other["duplicate_of"] is None
