from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import asset_out, job_out
from app.config import get_settings
from app.db.models import Asset, Project
from app.errors import VideoModuleError
from app.api.serializers import generation_out
from app.db.models import GenerationRecord
from app.generation.base import ADAPTER_CONTRACT_VERSION, GENERATION_KINDS, ImageGenerationRequest
from app.generation.registry import canonical_name, get_provider, list_providers
from app.generation.service import budget_commitment, ensure_requestable, estimate, project_spend, validate_request
from app.jobs.queue import create_job
from app.pipeline.graphics import create_map_asset, create_stat_asset
from app.pipeline.import_assets import (
    ALLOWED_ASSET_TYPES, ALLOWED_RIGHTS, ALLOWED_SOURCE_TYPES, _norm_rect, sanitize_text, sha256_of_file,
    write_source_metadata,
)
from app.pipeline.import_assets import import_asset as do_import
from app.storage import resolve_stored_path, to_rel

router = APIRouter(tags=["assets"])

_CHUNK = 1 << 20


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise VideoModuleError("PAYLOAD_TOO_LARGE", f"Upload exceeds the {limit} byte limit.")
    return bytes(buf)


@router.post("/assets/import", status_code=201)
async def import_asset_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    project_id: str = Form(...),
    asset_type: str = Form(...),
    source_type: str = Form("UNKNOWN"),
    title: str | None = Form(None),
    description: str | None = Form(None),
    creator: str | None = Form(None),
    licence: str | None = Form(None),
    attribution: str | None = Form(None),
    rights_status: str = Form("UNKNOWN"),
    source_url: str | None = Form(None),
    duration: float | None = Form(None),
    editorial_category: str | None = Form(None),
    extra_metadata: str | None = Form(None, description="JSON object: tags, regions, protected_regions, focus_point, clip"),
    source_identity: str | None = Form(None, description="Publisher / archive / record identifier of the source"),
    licence_url: str | None = Form(None),
    usage_rights: str | None = Form(None, description="Usage terms or restrictions"),
    attribution_required: bool = Form(False),
    source_retrieved_at: str | None = Form(None, description="ISO-8601 time the source was retrieved"),
    file: UploadFile | None = File(None),
):
    settings = get_settings()
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > settings.max_upload_bytes + 1024 * 1024:
        raise VideoModuleError("PAYLOAD_TOO_LARGE", "Request body exceeds the upload limit.")
    meta = None
    if extra_metadata:
        if len(extra_metadata) > settings.max_metadata_json_bytes:
            raise VideoModuleError("PAYLOAD_TOO_LARGE", "extra_metadata exceeds the size limit.")
        try:
            meta = json.loads(extra_metadata)
        except json.JSONDecodeError as exc:
            raise VideoModuleError("VALIDATION_ERROR", "extra_metadata must be valid JSON.") from exc
        if not isinstance(meta, dict):
            raise VideoModuleError("VALIDATION_ERROR", "extra_metadata must be a JSON object.")
    file_bytes = await _read_limited(file, settings.max_upload_bytes) if file is not None and file.filename else None
    asset, dedup = do_import(
        db, project_id=project_id, asset_type=asset_type, source_type=source_type, file_bytes=file_bytes,
        original_filename=file.filename if file else None, title=title, description=description, creator=creator,
        licence=licence, attribution=attribution, rights_status=rights_status, source_url=source_url,
        duration=duration, editorial_category=editorial_category, extra_metadata=meta,
        source_identity=source_identity, licence_url=licence_url, usage_rights=usage_rights,
        attribution_required=attribution_required, source_retrieved_at=source_retrieved_at,
    )
    return JSONResponse(status_code=200 if dedup else 201, content=asset_out(asset, deduplicated=dedup))


@router.get("/assets")
def list_assets(project_id: str | None = None, asset_type: str | None = None, source_type: str | None = None,
                rights_status: str | None = None, is_mock: bool | None = None, generation_kind: str | None = None,
                limit: int = 500, offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(Asset)
    if generation_kind:
        if generation_kind not in GENERATION_KINDS:
            raise VideoModuleError("VALIDATION_ERROR", f"generation_kind must be one of {sorted(GENERATION_KINDS)}")
        q = q.filter(Asset.generation_kind == generation_kind)
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    if asset_type:
        if asset_type not in ALLOWED_ASSET_TYPES:
            raise VideoModuleError("VALIDATION_ERROR", "Unknown asset_type filter.")
        q = q.filter(Asset.asset_type == asset_type)
    if source_type:
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise VideoModuleError("VALIDATION_ERROR", "Unknown source_type filter.")
        q = q.filter(Asset.source_type == source_type)
    if rights_status:
        if rights_status not in ALLOWED_RIGHTS:
            raise VideoModuleError("VALIDATION_ERROR", "Unknown rights_status filter.")
        q = q.filter(Asset.rights_status == rights_status)
    if is_mock is not None:
        q = q.filter(Asset.is_mock.is_(is_mock))
    rows = q.order_by(Asset.acquired_at.desc()).offset(max(0, offset)).limit(max(1, min(2000, limit))).all()
    return [asset_out(a) for a in rows]


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise VideoModuleError("NOT_FOUND", f"Asset {asset_id} not found.")
    return asset_out(asset)


@router.get("/assets/{asset_id}/file")
def get_asset_file(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if asset is None or not asset.local_path:
        raise VideoModuleError("NOT_FOUND", "Asset has no stored file.")
    path = resolve_stored_path(asset.local_path)
    if path is None or not path.is_file():
        raise VideoModuleError("SOURCE_UNAVAILABLE", "Stored file is missing on disk.", status_code=410)
    return FileResponse(path, media_type=asset.mime_type or "application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@router.get("/assets/{asset_id}/preview")
def get_asset_preview(asset_id: str, t: float = 0.0, page: int = 1, max_side: int = 640,
                      db: Session = Depends(get_db)):
    """Real preview PNG: image thumbnail, video frame at `t` seconds, or PDF page `page`.
    409 PREVIEW_UNAVAILABLE (with the exact reason) when no decoder is available or decoding fails."""
    import io as _io

    from fastapi.responses import Response

    from app.pipeline.media_probe import PreviewUnavailable
    from app.pipeline.preview import load_source_image

    asset = db.get(Asset, asset_id)
    if asset is None:
        raise VideoModuleError("NOT_FOUND", f"Asset {asset_id} not found.")
    if asset.local_path and resolve_stored_path(asset.local_path) is None:
        raise VideoModuleError("PATH_TRAVERSAL_REJECTED", "Stored path escapes module storage.")
    max_side = max(32, min(2048, max_side))
    try:
        img = load_source_image({"mime_type": asset.mime_type, "local_path": asset.local_path},
                                at_seconds=max(0.0, t), page=max(1, page))
    except PreviewUnavailable as exc:
        raise VideoModuleError("PREVIEW_UNAVAILABLE", f"Preview unavailable for {asset.asset_type}: {exc.reason}.") from exc
    img.thumbnail((max_side, max_side))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"X-Preview-Kind": ("video_frame" if (asset.mime_type or "").startswith("video/") else
                                                "pdf_page" if asset.mime_type == "application/pdf" else "image"),
                             "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------ generation
class GenerateRequest(BaseModel):
    project_id: str
    prompt: str = Field(min_length=1, max_length=2000)
    aspect_ratio: str = "16:9"
    resolution: str = "1280x720"
    quality: str = "standard"
    purpose: str | None = None
    scene_id: str | None = None
    reference_assets: list[str] = Field(default_factory=list, max_length=10)
    provider: str | None = None
    # Phase 2: if a REAL AI provider fails / is not configured, accept a clearly labelled PLACEHOLDER instead.
    # None = use the operator setting VIDEO_MODULE_ALLOW_PLACEHOLDER_FALLBACK (default false).
    allow_placeholder_fallback: bool | None = None


def _gen_req(body: GenerateRequest) -> ImageGenerationRequest:
    return validate_request(ImageGenerationRequest(
        prompt=body.prompt, aspect_ratio=body.aspect_ratio, resolution=body.resolution, quality=body.quality,
        purpose=body.purpose, scene_id=body.scene_id, reference_assets=body.reference_assets))


@router.get("/providers")
def providers():
    s = get_settings()
    return {"default": canonical_name(s.default_image_provider), "allow_placeholder_fallback": s.allow_placeholder_fallback,
            "adapter_contract_version": ADAPTER_CONTRACT_VERSION, "providers": list_providers()}


@router.get("/providers/{name}/status")
def provider_status(name: str, probe: bool = False):
    """Configuration check. NEVER generates an image. With probe=true and credentials configured, a billable
    provider is probed with one non-generating metadata request (e.g. GET /v1/models/{model})."""
    p = get_provider(name)
    out = {**p.describe(), "probe_requested": probe}
    if probe:
        out["probe"] = p.check_connectivity()
        out["state"] = out["probe"]["state"]
    else:
        out["probe"] = {"state": out["state"], "probed": False, "detail": "configuration only; no network request"}
    return out


@router.post("/assets/generate/estimate")
def estimate_generation(body: GenerateRequest, db: Session = Depends(get_db)):
    provider = body.provider or get_settings().default_image_provider
    est = estimate(provider, _gen_req(body))
    project = db.get(Project, body.project_id)
    if project is not None:
        committed, unknown_n = budget_commitment(db, body.project_id)
        within = None if (est["billable"] and est["estimated_cost"] is None) else (
            (not est["billable"]) or committed + est["estimated_cost"] <= project.ai_image_budget_usd + 1e-9)
        est.update(budget_usd=project.ai_image_budget_usd, spent_usd=round(project_spend(db, body.project_id), 6),
                   committed_usd=round(committed, 6), unknown_cost_generations=unknown_n, within_budget=within)
    return est


@router.post("/assets/generate", status_code=202)
def generate_asset(body: GenerateRequest, db: Session = Depends(get_db),
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    provider = canonical_name(body.provider or get_settings().default_image_provider)
    req = _gen_req(body)
    if db.get(Project, body.project_id) is None:
        raise VideoModuleError("NOT_FOUND", f"Project {body.project_id} not found.")
    ensure_requestable(provider, body.allow_placeholder_fallback)
    payload = {**body.model_dump(), "provider": provider, "prompt": req.prompt}
    job, reused = create_job(db, "generate_image", payload, project_id=body.project_id,
                             idempotency_key=sanitize_text(idempotency_key, 128))
    return job_out(job, reused=reused)


@router.get("/generations")
def list_generations(project_id: str, limit: int = 200, db: Session = Depends(get_db)):
    rows = (db.query(GenerationRecord).filter(GenerationRecord.project_id == project_id)
            .order_by(GenerationRecord.created_at.desc()).limit(max(1, min(1000, limit))).all())
    return [generation_out(g) for g in rows]


@router.get("/generations/{generation_id}")
def get_generation(generation_id: str, db: Session = Depends(get_db)):
    g = db.get(GenerationRecord, generation_id)
    if g is None:
        raise VideoModuleError("NOT_FOUND", f"Generation {generation_id} not found.")
    return generation_out(g)


# ------------------------------------------------------------------ variants
class VariantRequest(BaseModel):
    crop: dict = Field(description="Normalized rectangle {x,y,w,h} in 0..1 of the source image")
    title: str | None = None
    note: str | None = Field(default=None, max_length=500)


@router.post("/assets/{asset_id}/variants", status_code=201)
def create_variant(asset_id: str, body: VariantRequest, db: Session = Depends(get_db)):
    """Creates a REAL derived image (cropped pixels saved as a new file) that keeps the
    source's provenance, rights and editorial category. Rejects crops that would cut
    protected regions (spec §17)."""
    base = db.get(Asset, asset_id)
    if base is None:
        raise VideoModuleError("NOT_FOUND", f"Asset {asset_id} not found.")
    if not (base.mime_type or "").startswith("image/"):
        raise VideoModuleError("ASSET_INVALID", "Variants can only be derived from image assets.")
    rect = _norm_rect(body.crop, "crop")
    for pr in (base.extra_metadata or {}).get("protected_regions") or []:
        if not (pr["x"] >= rect["x"] - 1e-4 and pr["y"] >= rect["y"] - 1e-4 and
                pr["x"] + pr["w"] <= rect["x"] + rect["w"] + 1e-4 and pr["y"] + pr["h"] <= rect["y"] + rect["h"] + 1e-4):
            raise VideoModuleError("EDITORIAL_POLICY", f"Crop would cut protected region '{pr.get('label')}'.")
    src = resolve_stored_path(base.local_path)
    if src is None or not src.is_file():
        raise VideoModuleError("SOURCE_UNAVAILABLE", "Source file missing.", status_code=410)
    with Image.open(src) as im:
        im.load()
        box = (int(rect["x"] * im.width), int(rect["y"] * im.height),
               int((rect["x"] + rect["w"]) * im.width), int((rect["y"] + rect["h"]) * im.height))
        if box[2] - box[0] < 16 or box[3] - box[1] < 16:
            raise VideoModuleError("ASSET_INVALID", "Variant crop is too small (min 16 px per side).")
        out = src.parent / f"{src.stem}__v{box[0]}_{box[1]}_{box[2]}_{box[3]}.png"
        cropped = im.crop(box)
        cropped.save(out, format="PNG")
        w, h = cropped.size
    meta = dict(base.extra_metadata or {})
    meta.pop("regions", None)
    meta.pop("protected_regions", None)
    meta.pop("focus_point", None)
    meta.update(variant_of=base.asset_id, variant_crop=rect, variant_note=sanitize_text(body.note, 500),
                base_checksum_sha256=base.checksum_sha256)
    variant = Asset(
        project_id=base.project_id, asset_type=base.asset_type, source_type=base.source_type,
        source_url=base.source_url, local_path=to_rel(out), mime_type="image/png", file_size_bytes=out.stat().st_size,
        title=sanitize_text(body.title, 255) or f"{base.title or 'asset'} (crop variant)", description=base.description,
        creator=base.creator, licence=base.licence, attribution=base.attribution, rights_status=base.rights_status,
        checksum_sha256=sha256_of_file(out), width=w, height=h, duration=base.duration, provider=base.provider,
        model=base.model, is_mock=base.is_mock, editorial_category=base.editorial_category, extra_metadata=meta,
        generation_kind=base.generation_kind, cost_status=("NOT_APPLICABLE" if base.cost_status else None),
        is_fallback=base.is_fallback, fallback_from=base.fallback_from, fallback_reason=base.fallback_reason,
        source_identity=base.source_identity, licence_url=base.licence_url, usage_rights=base.usage_rights,
        attribution_required=base.attribution_required, source_retrieved_at=base.source_retrieved_at,
    )
    db.add(variant)
    db.commit()
    write_source_metadata(variant)
    return asset_out(variant)


# ------------------------------------------------------------------ first-class maps / stats
class MapCreate(BaseModel):
    project_id: str
    spec: dict
    tags: list[str] = Field(default_factory=list, max_length=50)


class StatCreate(BaseModel):
    project_id: str
    spec: dict
    tags: list[str] = Field(default_factory=list, max_length=50)


@router.post("/assets/maps", status_code=201)
def create_map(body: MapCreate, db: Session = Depends(get_db)):
    return asset_out(create_map_asset(db, body.project_id, body.spec, body.tags))


@router.post("/assets/stats", status_code=201)
def create_stat(body: StatCreate, db: Session = Depends(get_db)):
    return asset_out(create_stat_asset(db, body.project_id, body.spec, body.tags))
