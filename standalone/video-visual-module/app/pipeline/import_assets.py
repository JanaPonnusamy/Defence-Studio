"""
Asset import stage (spec §4/§5/§6/§32).

Enforced here:
  * content validation: images decoded fully with Pillow (format allow-list,
    integrity/truncation, dimension limits, decompression-bomb guard); PDFs
    checked for header + EOF trailer; video clips checked by container magic
  * size limit (max_upload_bytes) -- enforced while streaming in the API too
  * filenames never used for paths except as a sanitized suffix; final path
    is verified to stay inside module storage (no path traversal)
  * metadata strings sanitized (control chars stripped, length-capped);
    structured metadata size/depth-limited and stored as inert data only
  * source_url validated (http/https only, no embedded credentials)
  * sha256 checksum computed; identical content in the same project is
    de-duplicated (existing asset returned, flagged `deduplicated`)
  * rights: UNKNOWN is never presented as open; OPEN requires a named licence
    and a known source; AI imagery can never be labelled as external evidence
"""
from __future__ import annotations

import hashlib
import io
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Asset, Project
from app.errors import VideoModuleError
from app.storage import to_rel, write_json_atomic

ALLOWED_ASSET_TYPES = {
    "IMAGE", "VIDEO_CLIP", "MAP", "DIAGRAM", "CHART", "STAT_CARD", "DOCUMENT",
    "SATELLITE", "SOURCE_PHOTO", "AI_IMAGE", "TITLE_CARD", "TRANSITION",
    "PLACEHOLDER_IMAGE",  # Phase 2: module-generated placeholder art (never importable, never AI, never evidence)
}
GENERATED_ONLY_ASSET_TYPES = {"PLACEHOLDER_IMAGE"}
IMAGE_ASSET_TYPES = ALLOWED_ASSET_TYPES - {"VIDEO_CLIP", "DOCUMENT", "TRANSITION"}
ALLOWED_SOURCE_TYPES = {"EXTERNAL_SOURCE", "USER_PROVIDED", "AI_GENERATED", "SYSTEM_GENERATED", "UNKNOWN"}
ALLOWED_RIGHTS = {"OPEN", "LICENSED", "PERMITTED", "UNKNOWN", "RESTRICTED"}
USABLE_RIGHTS = {"OPEN", "LICENSED", "PERMITTED"}
EDITORIAL_CATEGORIES = {"FACTUAL_EVIDENCE", "ILLUSTRATIVE_VISUAL", "AI_GENERATED", "PLACEHOLDER", "UNKNOWN"}
IMPORTABLE_EDITORIAL_CATEGORIES = EDITORIAL_CATEGORIES - {"PLACEHOLDER"}
NEAR_DUPLICATE_MAX_DISTANCE = 4  # dHash Hamming distance (of 64 bits) treated as a near-duplicate
ALLOWED_IMAGE_FORMATS = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"),
                         "WEBP": ("image/webp", "webp"), "GIF": ("image/gif", "gif")}

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.\-]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_OPEN_CLAIM_RE = re.compile(r"(?i)open[\s-]*source|public[\s-]*domain|creative\s*commons|\bcc[\s-]?(by|0)")


# --------------------------------------------------------------- sanitizers
def sanitize_filename(name: str | None) -> str:
    name = Path(str(name or "asset").replace("\\", "/")).name
    name = _UNSAFE_FILENAME_RE.sub("_", name).lstrip(".")
    return name[:120] or "asset"


def sanitize_text(value, max_len: int = 2000) -> str | None:
    if value is None:
        return None
    text = _CONTROL_RE.sub("", str(value)).strip()
    return text[:max_len] or None


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_url(url: str | None) -> str | None:
    url = sanitize_text(url, 2000)
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise VideoModuleError("INVALID_URL", "source_url must use http or https.")
    if not parsed.hostname:
        raise VideoModuleError("INVALID_URL", "source_url must include a host.")
    if parsed.username or parsed.password:
        raise VideoModuleError("INVALID_URL", "source_url must not embed credentials.")
    if any(c.isspace() for c in url):
        raise VideoModuleError("INVALID_URL", "source_url must not contain whitespace.")
    return url


def sanitize_metadata(value, depth: int = 0):
    """Return an inert, JSON-safe, size-bounded copy of structured metadata."""
    if depth > 6:
        raise VideoModuleError("VALIDATION_ERROR", "extra_metadata nested too deeply.")
    if isinstance(value, dict):
        if len(value) > 200:
            raise VideoModuleError("VALIDATION_ERROR", "extra_metadata has too many keys.")
        return {sanitize_text(str(k), 64) or "_": sanitize_metadata(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 500:
            raise VideoModuleError("VALIDATION_ERROR", "extra_metadata list too long.")
        return [sanitize_metadata(v, depth + 1) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            raise VideoModuleError("VALIDATION_ERROR", "extra_metadata numbers must be finite.")
        return value
    return sanitize_text(value, 4000)


def _norm_rect(r: dict, what: str) -> dict:
    try:
        x, y, w, h = (float(r[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoModuleError("VALIDATION_ERROR", f"{what} needs numeric x,y,w,h in 0..1.") from exc
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
        raise VideoModuleError("VALIDATION_ERROR", f"{what} must be a normalized rectangle inside 0..1.")
    out = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
    if "label" in r:
        out["label"] = sanitize_text(r.get("label"), 120)
    if "page" in r:
        try:
            out["page"] = max(1, int(r["page"]))
        except (TypeError, ValueError) as exc:
            raise VideoModuleError("VALIDATION_ERROR", f"{what}.page must be an integer.") from exc
    return out


def normalize_structured_metadata(meta: dict, duration: float | None) -> dict:
    meta = sanitize_metadata(meta or {})
    if "tags" in meta:
        tags = meta["tags"] if isinstance(meta["tags"], list) else str(meta["tags"]).split(",")
        meta["tags"] = sorted({t.strip().lower() for t in tags if isinstance(t, str) and t.strip()})[:50]
    for key in ("protected_regions", "regions"):
        if key in meta:
            if not isinstance(meta[key], list):
                raise VideoModuleError("VALIDATION_ERROR", f"{key} must be a list of rectangles.")
            meta[key] = [_norm_rect(r, key) for r in meta[key] if isinstance(r, dict)]
    if "focus_point" in meta:
        fp = meta["focus_point"]
        try:
            fx, fy = float(fp["x"]), float(fp["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VideoModuleError("VALIDATION_ERROR", "focus_point needs numeric x,y in 0..1.") from exc
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            raise VideoModuleError("VALIDATION_ERROR", "focus_point must be within 0..1.")
        meta["focus_point"] = {"x": fx, "y": fy}
    if "clip" in meta:
        clip = meta["clip"]
        try:
            i, o = float(clip["in_point"]), float(clip["out_point"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VideoModuleError("VALIDATION_ERROR", "clip needs numeric in_point/out_point seconds.") from exc
        if not (0 <= i < o) or (duration is not None and o > duration + 1e-6):
            raise VideoModuleError("VALIDATION_ERROR", "clip in/out points must satisfy 0 <= in < out <= duration.")
        meta["clip"] = {"in_point": i, "out_point": o, "duration": round(o - i, 3)}
    return meta


# --------------------------------------------------------------- content validation
def validate_image_bytes(data: bytes) -> dict:
    settings = get_settings()
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            im.verify()  # structural integrity (CRC etc.)
        with Image.open(io.BytesIO(data)) as im:
            im.load()  # full decode -> catches truncation
            width, height = im.size
    except Image.DecompressionBombError as exc:
        raise VideoModuleError("ASSET_INVALID", "Image exceeds the maximum allowed pixel count.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise VideoModuleError("ASSET_INVALID", "File is not a valid, complete image (corrupt or unsupported).") from exc
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise VideoModuleError("ASSET_INVALID", f"Unsupported image format {fmt}; allowed: {sorted(ALLOWED_IMAGE_FORMATS)}.")
    if min(width, height) < settings.min_image_side or max(width, height) > settings.max_image_side:
        raise VideoModuleError(
            "ASSET_INVALID",
            f"Image dimensions {width}x{height} outside allowed range "
            f"{settings.min_image_side}..{settings.max_image_side} px per side.",
        )
    mime, ext = ALLOWED_IMAGE_FORMATS[fmt]
    return {"kind": "image", "mime": mime, "ext": ext, "width": width, "height": height}


def validate_pdf_bytes(data: bytes) -> dict:
    if not data.startswith(b"%PDF-"):
        raise VideoModuleError("ASSET_INVALID", "Document is not a PDF (missing %PDF- header).")
    if b"%%EOF" not in data[-2048:]:
        raise VideoModuleError("ASSET_INVALID", "PDF appears truncated (missing %%EOF trailer).")
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", data))
    return {"kind": "pdf", "mime": "application/pdf", "ext": "pdf", "width": None, "height": None, "pages": pages}


def validate_video_bytes(data: bytes) -> dict:
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return {"kind": "video", "mime": "video/mp4", "ext": "mp4", "width": None, "height": None}
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return {"kind": "video", "mime": "video/webm", "ext": "webm", "width": None, "height": None}
    if data.startswith(b"RIFF") and data[8:12] == b"AVI ":
        return {"kind": "video", "mime": "video/x-msvideo", "ext": "avi", "width": None, "height": None}
    raise VideoModuleError("ASSET_INVALID", "Video clip container not recognised (mp4/mov, webm/mkv, avi supported).")


def validate_content(asset_type: str, data: bytes) -> dict:
    if asset_type == "VIDEO_CLIP":
        return validate_video_bytes(data)
    if asset_type == "DOCUMENT" and data.startswith(b"%PDF-"):
        return validate_pdf_bytes(data)
    return validate_image_bytes(data)


def storage_dir_for(asset_type: str) -> Path:
    s = get_settings()
    return {
        "MAP": s.maps_dir, "DOCUMENT": s.documents_dir, "VIDEO_CLIP": s.clips_dir,
        "STAT_CARD": s.graphics_dir, "CHART": s.graphics_dir, "AI_IMAGE": s.generated_dir,
    }.get(asset_type, s.assets_dir)


# --------------------------------------------------------------- rights / editorial
def default_editorial_category(asset_type: str, source_type: str) -> str:
    if source_type == "AI_GENERATED":
        return "AI_GENERATED"
    if source_type == "UNKNOWN":
        return "UNKNOWN"
    if asset_type in ("TITLE_CARD", "TRANSITION", "DIAGRAM"):
        return "ILLUSTRATIVE_VISUAL"
    if source_type in ("EXTERNAL_SOURCE", "USER_PROVIDED"):
        return "FACTUAL_EVIDENCE"
    return "ILLUSTRATIVE_VISUAL"


def provenance_class(asset: Asset) -> str:
    """Single honest classification of where an asset's pixels came from."""
    kind = getattr(asset, "generation_kind", None) or "NOT_GENERATED"
    if kind == "TEST_FIXTURE" or asset.is_mock:
        return "TEST_FIXTURE"
    if kind == "PLACEHOLDER" or asset.asset_type == "PLACEHOLDER_IMAGE":
        return "PLACEHOLDER"
    if kind == "AI_GENERATED" or asset.source_type == "AI_GENERATED":
        return "AI_GENERATED"
    if asset.source_type in ("EXTERNAL_SOURCE", "USER_PROVIDED"):
        return "ORIGINAL_SOURCE"
    if asset.source_type == "SYSTEM_GENERATED":
        return "SYSTEM_GRAPHIC"  # maps / stat cards rendered from supplied data
    return "UNKNOWN_ORIGIN"


def evidence_eligible(asset: Asset) -> bool:
    """Only rights-cleared original sources, or graphics rendered from cited source data, categorised as
    FACTUAL_EVIDENCE can be presented as evidence. AI, placeholder, fixture and unknown-origin assets never
    can, and neither can anything with UNKNOWN/RESTRICTED rights (spec §8/§34)."""
    return (provenance_class(asset) in ("ORIGINAL_SOURCE", "SYSTEM_GRAPHIC")
            and asset.editorial_category == "FACTUAL_EVIDENCE" and asset.rights_status in USABLE_RIGHTS)


def rights_usable(asset: Asset) -> bool:
    return asset.rights_status in USABLE_RIGHTS


def dhash_hex(img: Image.Image) -> str:
    """64-bit difference hash (perceptual) for near-duplicate detection."""
    g = img.convert("L").resize((9, 8), Image.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _parse_ts(value: str | None, what: str):
    from datetime import datetime, timezone

    value = sanitize_text(value, 64)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VideoModuleError("VALIDATION_ERROR", f"{what} must be an ISO-8601 timestamp.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt > datetime.now(timezone.utc):
        raise VideoModuleError("VALIDATION_ERROR", f"{what} cannot be in the future.")
    return dt


def rights_label(rights_status: str, licence: str | None) -> str:
    if rights_status == "OPEN":
        return f"OPEN licence ({licence})"
    if rights_status == "LICENSED":
        return f"LICENSED ({licence or 'licence terms on file'})"
    if rights_status == "PERMITTED":
        return "PERMITTED for use"
    if rights_status == "RESTRICTED":
        return "RESTRICTED - do not use"
    return "UNKNOWN rights - not cleared for reuse (never treat as open source)"


def check_rights(source_type: str, rights_status: str, licence: str | None) -> None:
    if rights_status not in ALLOWED_RIGHTS:
        raise VideoModuleError("VALIDATION_ERROR", f"rights_status must be one of {sorted(ALLOWED_RIGHTS)}")
    if rights_status == "OPEN":
        if source_type == "UNKNOWN":
            raise VideoModuleError("RIGHTS_UNKNOWN", "An asset of UNKNOWN source cannot be marked OPEN.")
        if not licence:
            raise VideoModuleError("RIGHTS_UNKNOWN", "rights_status OPEN requires a named licence.")
    if rights_status == "UNKNOWN" and licence and _OPEN_CLAIM_RE.search(licence):
        raise VideoModuleError(
            "RIGHTS_UNKNOWN",
            "Licence text claims an open licence while rights_status is UNKNOWN; verify rights and set rights_status explicitly.",
        )


# --------------------------------------------------------------- import
def import_asset(
    db: Session,
    *,
    project_id: str,
    asset_type: str,
    source_type: str,
    file_bytes: bytes | None,
    original_filename: str | None,
    title: str | None = None,
    description: str | None = None,
    creator: str | None = None,
    licence: str | None = None,
    attribution: str | None = None,
    rights_status: str = "UNKNOWN",
    source_url: str | None = None,
    duration: float | None = None,
    editorial_category: str | None = None,
    extra_metadata: dict | None = None,
    source_identity: str | None = None,
    licence_url: str | None = None,
    usage_rights: str | None = None,
    attribution_required: bool = False,
    source_retrieved_at: str | None = None,
) -> tuple[Asset, bool]:
    """Returns (asset, deduplicated)."""
    settings = get_settings()
    if db.get(Project, project_id) is None:
        raise VideoModuleError("NOT_FOUND", f"Project {project_id} not found.")
    if asset_type not in ALLOWED_ASSET_TYPES:
        raise VideoModuleError("VALIDATION_ERROR", f"asset_type must be one of {sorted(ALLOWED_ASSET_TYPES)}")
    if asset_type in GENERATED_ONLY_ASSET_TYPES:
        raise VideoModuleError("EDITORIAL_POLICY", f"{asset_type} assets are created only by the module's placeholder "
                               "provider and cannot be imported.")
    if editorial_category is not None and editorial_category not in IMPORTABLE_EDITORIAL_CATEGORIES:
        raise VideoModuleError("VALIDATION_ERROR",
                               f"editorial_category must be one of {sorted(IMPORTABLE_EDITORIAL_CATEGORIES)}")
    attribution_clean = sanitize_text(attribution, 1000)
    if attribution_required and not attribution_clean:
        raise VideoModuleError("RIGHTS_UNKNOWN", "attribution_required is set but no attribution text was supplied.")
    retrieved_at = _parse_ts(source_retrieved_at, "source_retrieved_at")
    licence_url_clean = validate_url(licence_url) if licence_url else None
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise VideoModuleError("VALIDATION_ERROR", f"source_type must be one of {sorted(ALLOWED_SOURCE_TYPES)}")
    # §8: AI imagery is always labelled AI_GENERATED, and only AI imagery may carry that label as AI_IMAGE.
    if asset_type == "AI_IMAGE" and source_type != "AI_GENERATED":
        raise VideoModuleError("EDITORIAL_POLICY", "AI_IMAGE assets must have source_type AI_GENERATED.")
    if source_type == "AI_GENERATED" and asset_type != "AI_IMAGE":
        raise VideoModuleError("EDITORIAL_POLICY", "AI_GENERATED imports must use asset_type AI_IMAGE (AI is not evidence).")
    licence = sanitize_text(licence, 128)
    check_rights(source_type, rights_status, licence)
    category = editorial_category or default_editorial_category(asset_type, source_type)
    if category not in EDITORIAL_CATEGORIES:
        raise VideoModuleError("VALIDATION_ERROR", f"editorial_category must be one of {sorted(EDITORIAL_CATEGORIES)}")
    if source_type == "AI_GENERATED" and category != "AI_GENERATED":
        raise VideoModuleError("EDITORIAL_POLICY", "AI-generated assets cannot be categorised as evidence/illustrative-source.")
    if category == "FACTUAL_EVIDENCE" and source_type in ("UNKNOWN", "SYSTEM_GENERATED"):
        raise VideoModuleError("EDITORIAL_POLICY", "Only external/user-provided sources can be FACTUAL_EVIDENCE.")
    if duration is not None and not (0 < float(duration) < 24 * 3600):
        raise VideoModuleError("VALIDATION_ERROR", "duration must be positive seconds.")
    # Phase 2: VIDEO_CLIP duration may be omitted when the optional ffmpeg probe can read it (checked after the
    # file is stored); if the probe is unavailable or fails, duration remains required.
    url = validate_url(source_url)
    meta = normalize_structured_metadata(extra_metadata or {}, duration)
    raw = len(repr(meta).encode("utf-8"))
    if raw > settings.max_metadata_json_bytes:
        raise VideoModuleError("PAYLOAD_TOO_LARGE", "extra_metadata exceeds the size limit.")

    phash, near_dup = None, None
    if file_bytes is None:
        if asset_type != "TRANSITION":
            raise VideoModuleError("ASSET_INVALID", "A file is required for this asset_type.")
        info = {"mime": None, "width": None, "height": None}
        checksum = None
        rel_path = None
        size = None
    else:
        if len(file_bytes) == 0:
            raise VideoModuleError("ASSET_INVALID", "Uploaded file is empty.")
        if len(file_bytes) > settings.max_upload_bytes:
            raise VideoModuleError("PAYLOAD_TOO_LARGE", "Uploaded file exceeds max_upload_bytes.")
        info = validate_content(asset_type, file_bytes)
        checksum = sha256_of_bytes(file_bytes)
        existing = db.query(Asset).filter(Asset.project_id == project_id, Asset.checksum_sha256 == checksum).first()
        if existing is not None:
            return existing, True
        if info["kind"] == "image":
            with Image.open(io.BytesIO(file_bytes)) as im:
                phash = dhash_hex(im)
            for other_id, other_hash in db.query(Asset.asset_id, Asset.perceptual_hash).filter(
                    Asset.project_id == project_id, Asset.perceptual_hash.isnot(None)).all():
                if hamming_hex(phash, other_hash) <= NEAR_DUPLICATE_MAX_DISTANCE:
                    near_dup = other_id
                    break
        dest_dir = (storage_dir_for(asset_type) / project_id).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(sanitize_filename(original_filename)).stem[:60] or "asset"
        dest = (dest_dir / f"{checksum[:16]}_{stem}.{info['ext']}").resolve()
        if dest_dir not in dest.parents:
            raise VideoModuleError("PATH_TRAVERSAL_REJECTED", "Resolved asset path escapes storage root.")
        dest.write_bytes(file_bytes)
        rel_path = to_rel(dest)
        size = len(file_bytes)
        if info["kind"] == "video":
            duration, meta = _probe_video_on_import(dest, duration, meta, info)
        if info["kind"] == "pdf":
            from app.pipeline.media_probe import PreviewUnavailable, pdf_page_count  # noqa: PLC0415

            try:
                info["pages"] = pdf_page_count(dest)
                meta["page_count_source"] = "pdfium"
            except PreviewUnavailable as exc:
                meta["page_count_source"] = f"header scan ({exc.reason})"
        if info.get("pages") is not None:
            meta.setdefault("pages", info["pages"])

    asset = Asset(
        project_id=project_id,
        asset_type=asset_type,
        source_type=source_type,
        source_url=url,
        local_path=rel_path,
        mime_type=info.get("mime"),
        file_size_bytes=size,
        title=sanitize_text(title, 255) or (sanitize_filename(original_filename) if original_filename else None),
        description=sanitize_text(description, 4000),
        creator=sanitize_text(creator, 255),
        licence=licence,
        attribution=attribution_clean,
        rights_status=rights_status,
        checksum_sha256=checksum,
        width=info.get("width"),
        height=info.get("height"),
        duration=float(duration) if duration is not None else None,
        editorial_category=category,
        is_mock=False,
        extra_metadata=meta,
        generation_kind="AI_GENERATED" if source_type == "AI_GENERATED" else "NOT_GENERATED",
        source_identity=sanitize_text(source_identity, 255),
        licence_url=licence_url_clean,
        usage_rights=sanitize_text(usage_rights, 2000),
        attribution_required=bool(attribution_required),
        source_retrieved_at=retrieved_at,
        perceptual_hash=phash,
        duplicate_of=near_dup,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    write_source_metadata(asset)
    return asset, False


def _probe_video_on_import(dest: Path, duration: float | None, meta: dict, info: dict) -> tuple[float | None, dict]:
    """Use the optional ffmpeg probe for real width/height/fps/duration. Never invents values: if the probe is
    unavailable and the caller gave no duration, the import is rejected (and the stored file removed)."""
    from app.pipeline.media_probe import PreviewUnavailable, probe_video  # noqa: PLC0415

    try:
        probe = probe_video(dest)
    except PreviewUnavailable as exc:
        if duration is None:
            dest.unlink(missing_ok=True)
            raise VideoModuleError("VALIDATION_ERROR", f"VIDEO_CLIP duration (seconds) is required: the media probe "
                                                       f"could not read it ({exc.reason}).") from exc
        meta["media_probe"] = {"available": False, "reason": exc.reason}
        return duration, meta
    info["width"], info["height"] = probe["width"], probe["height"]
    meta["media_probe"] = {"available": True, "fps": probe["fps"], "duration": probe["duration"],
                           "width": probe["width"], "height": probe["height"]}
    if duration is None:
        if probe["duration"] is None:
            dest.unlink(missing_ok=True)
            raise VideoModuleError("VALIDATION_ERROR", "VIDEO_CLIP duration is required: the container does not declare one.")
        duration = probe["duration"]
        meta["duration_source"] = "probe"
    else:
        meta["duration_source"] = "caller"
    clip = meta.get("clip")
    if clip and clip["out_point"] > duration + 1e-6:
        dest.unlink(missing_ok=True)
        raise VideoModuleError("VALIDATION_ERROR", "clip in/out points must satisfy 0 <= in < out <= duration.")
    return duration, meta


def write_source_metadata(asset: Asset) -> None:
    """Persist provenance/rights sidecar under storage/source_metadata (spec §26)."""
    write_json_atomic(
        get_settings().source_metadata_dir / f"{asset.asset_id}.json",
        {
            "asset_id": asset.asset_id, "project_id": asset.project_id, "asset_type": asset.asset_type,
            "source_type": asset.source_type, "source_url": asset.source_url, "title": asset.title,
            "creator": asset.creator, "licence": asset.licence, "attribution": asset.attribution,
            "rights_status": asset.rights_status, "rights_label": rights_label(asset.rights_status, asset.licence),
            "editorial_category": asset.editorial_category, "checksum_sha256": asset.checksum_sha256,
            "acquired_at": asset.acquired_at.isoformat() if asset.acquired_at else None,
            "provider": asset.provider, "model": asset.model, "is_mock": asset.is_mock,
            # Phase 2 provenance
            "generation_kind": asset.generation_kind, "provenance_class": provenance_class(asset),
            "evidence_eligible": evidence_eligible(asset), "source_identity": asset.source_identity,
            "licence_url": asset.licence_url, "usage_rights": asset.usage_rights,
            "attribution_required": bool(asset.attribution_required),
            "source_retrieved_at": asset.source_retrieved_at.isoformat() if asset.source_retrieved_at else None,
            "perceptual_hash": asset.perceptual_hash, "duplicate_of": asset.duplicate_of,
            "is_fallback": bool(asset.is_fallback), "fallback_from": asset.fallback_from,
            "provider_request_id": asset.provider_request_id, "cost_status": asset.cost_status,
            "generation_cost": asset.generation_cost,
        },
    )


def new_id() -> str:
    return str(uuid.uuid4())
