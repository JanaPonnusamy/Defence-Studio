"""Media context checks that only report what the bytes and tools actually show."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

_SHOWINFO_PTS = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_SHOWINFO_LINE = re.compile(r"\bn:\s*(\d+)\b.*?pts_time:(-?[0-9]+(?:\.[0-9]+)?)")

STATUSES = (
    "CONTEXT_MATCHED",
    "POSSIBLE_CONTEXT_MATCH",
    "RECYCLED_OR_OLD",
    "CONTEXT_MISMATCH",
    "MANIPULATION_SUSPECTED",
    "INSUFFICIENT_EVIDENCE",
    "RIGHTS_REVIEW_REQUIRED",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_kind(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"P6") or data.startswith(b"P3"):
        return "image/x-portable-pixmap"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    return "application/octet-stream"


def _png_chunks(data: bytes):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        start = pos + 8
        end = start + length
        if end + 4 > len(data):
            return
        yield tag, data[start:end]
        pos = end + 4


def png_text(data: bytes) -> dict[str, str]:
    found = {}
    for tag, payload in _png_chunks(data) or ():
        if tag != b"tEXt" or b"\x00" not in payload:
            continue
        key, value = payload.split(b"\x00", 1)
        found[key.decode("latin1", "replace")] = value.decode("latin1", "replace")
    return found


def _box_pixels(width: int, height: int, rgb: list[tuple[int, int, int]]):
    grid = []
    for gy in range(8):
        for gx in range(8):
            x0, x1 = gx * width // 8, (gx + 1) * width // 8
            y0, y1 = gy * height // 8, (gy + 1) * height // 8
            total, count = 0, 0
            for y in range(y0, max(y1, y0 + 1)):
                for x in range(x0, max(x1, x0 + 1)):
                    r, g, b = rgb[y * width + x]
                    total += (r + g + b) / 3
                    count += 1
            grid.append(total / count if count else 0)
    mean = sum(grid) / len(grid)
    bits = "".join("1" if sample >= mean else "0" for sample in grid)
    return f"{int(bits, 2):016x}"


def average_hash(data: bytes) -> str | None:
    kind = file_kind(data)
    if kind == "image/png":
        decoded = _decode_png(data)
        if decoded is None:
            return None
        width, height, rgb = decoded
        return _box_pixels(width, height, rgb)
    if kind == "image/x-portable-pixmap" and data.startswith(b"P6"):
        decoded = _decode_ppm(data)
        if decoded is None:
            return None
        width, height, rgb = decoded
        return _box_pixels(width, height, rgb)
    return None


def _decode_ppm(data: bytes):
    try:
        header, raw = data.split(b"\n", 3)[0], data
        lines = raw.split(b"\n", 3)
        if lines[0] != b"P6":
            return None
        width, height = (int(part) for part in lines[1].split())
        pixels = lines[3]
        if len(pixels) < width * height * 3:
            return None
        rgb = [tuple(pixels[i:i + 3]) for i in range(0, width * height * 3, 3)]
        return width, height, rgb
    except (ValueError, IndexError):
        return None


def _decode_png(data: bytes):
    width = height = None
    color = 2
    idat = b""
    for tag, payload in _png_chunks(data) or ():
        if tag == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", payload[:10])
            if depth != 8 or color not in {2, 6}:
                return None
        elif tag == b"IDAT":
            idat += payload
    if not width or not idat:
        return None
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return None
    channels = 3 if color == 2 else 4
    stride = width * channels
    rgb = []
    pos = 0
    previous = bytearray(stride)
    for _ in range(height):
        if pos >= len(raw):
            return None
        filt = raw[pos]
        row = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if filt == 0:
            pass
        elif filt == 1:
            for i in range(len(row)):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + left) & 255
        elif filt == 2:
            for i in range(len(row)):
                row[i] = (row[i] + previous[i]) & 255
        else:
            return None
        previous = row
        for i in range(0, stride, channels):
            rgb.append((row[i], row[i + 1], row[i + 2]))
    return width, height, rgb


def hamming(left: str | None, right: str | None) -> int | None:
    if not left or not right or len(left) != len(right):
        return None
    return (int(left, 16) ^ int(right, 16)).bit_count()


def jpeg_exif_datetime(data: bytes) -> str | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 4 < len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xDA:
            return None
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        payload = data[pos + 4:pos + 2 + length]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            return _exif_datetime(payload[6:])
        pos += 2 + length
    return None


def _exif_datetime(tiff: bytes) -> str | None:
    if tiff[:2] not in {b"II", b"MM"}:
        return None
    little = tiff[:2] == b"II"
    endian = "<" if little else ">"
    if struct.unpack(endian + "H", tiff[2:4])[0] != 42:
        return None
    offset = struct.unpack(endian + "I", tiff[4:8])[0]
    return _ifd_datetime(tiff, offset, endian)


def _ifd_datetime(tiff: bytes, offset: int, endian: str) -> str | None:
    if offset + 2 > len(tiff):
        return None
    count = struct.unpack(endian + "H", tiff[offset:offset + 2])[0]
    pos = offset + 2
    found = None
    for _ in range(count):
        if pos + 12 > len(tiff):
            break
        tag, typ, nvals, value = struct.unpack(endian + "HHII", tiff[pos:pos + 12])
        pos += 12
        if tag in {0x0132, 0x9003} and typ == 2:
            start = value if nvals > 4 else pos - 4
            raw = tiff[start:start + nvals]
            found = raw.split(b"\x00", 1)[0].decode("ascii", "replace")
        if tag == 0x8769:
            nested = _ifd_datetime(tiff, value, endian)
            if nested:
                found = nested
    return found


def _tokens(text: str) -> set[str]:
    return {part.lower() for part in (text or "").replace(",", " ").split() if len(part) >= 3}


def sample_video_frames(path: str) -> dict:
    """Sample up to three PNG frames. ``timestamp_ms`` is ffmpeg ``pts_time`` when showinfo prints it."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"available": False, "reason": "ffmpeg is not on PATH", "frames": None}
    import tempfile
    out_dir = Path(tempfile.mkdtemp(prefix="factcheck-frames-"))
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-y", "-i", path, "-vf", "fps=1,showinfo", "-frames:v", "3",
             str(out_dir / "frame-%02d.png")],
            capture_output=True, timeout=20, check=False,
        )
        log = (completed.stderr or b"").decode("utf-8", "replace")
        if completed.returncode != 0:
            return {"available": True, "reason": log[:400] or "ffmpeg failed", "frames": []}
        # Associate by showinfo's own output-frame number (n:), never by list position, so a
        # frame whose pts_time was not printed cannot shift later frames onto the wrong time.
        pts_by_frame = {}
        for line in log.splitlines():
            found = _SHOWINFO_LINE.search(line)
            if found and not found.group(2).startswith("-"):      # a negative pts is not a usable time
                pts_by_frame[int(found.group(1))] = int(round(float(found.group(2)) * 1000))
        pts = list(pts_by_frame.values())
        frames = []
        for index, frame in enumerate(sorted(out_dir.glob("frame-*.png"))):
            timestamp = pts_by_frame.get(index)
            frames.append({
                "timestamp_ms": timestamp,
                "sample_index": index,
                "timestamp_origin": "ffmpeg_showinfo_pts_time" if timestamp is not None else "unverified",
                "bytes": frame.read_bytes(),
                "note": (
                    f"ffmpeg showinfo pts_time converted to {timestamp} ms."
                    if timestamp is not None
                    else "The sample index is not a source timestamp; ffmpeg did not print pts_time for this frame."
                ),
            })
        return {"available": True, "reason": "sampled", "frames": frames, "showinfo_pts_count": len(pts)}
    except Exception as exc:
        return {"available": True, "reason": str(exc)[:400], "frames": []}
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def rights_status(asset_ref: str | None = None, registered_asset: dict | None = None) -> dict:
    """Factual context and reuse rights stay separate. This does not clear a file."""
    result = {
        "status": "RIGHTS_REVIEW_REQUIRED",
        "asset_ref": asset_ref,
        "source": "factcheck-rights-port",
        "engine": "defence_studio.services.assets",
        "standalone_are": "not drop-in compatible; see docs/asset_rights_integration/COMPATIBILITY.md",
        "detail": "Context verification does not grant reuse. A CLEAR verdict requires an existing AssetService review, not this engine.",
        "review_status": None,
        "effective_rights": None,
    }
    if not registered_asset:
        return result
    result["review_status"] = registered_asset.get("review_status")
    result["effective_rights"] = registered_asset.get("effective_rights")
    result["source"] = "defence_studio.services.assets"
    if registered_asset.get("review_status") == "APPROVED" and registered_asset.get("effective_rights") == "CLEAR":
        result["status"] = "CLEARED"
        result["detail"] = "AssetService already records an approved CLEAR rights decision. Fact-check did not grant it."
    else:
        result["detail"] = (
            f"Asset review is {registered_asset.get('review_status')} and effective rights are "
            f"{registered_asset.get('effective_rights')}. That is not a fact-check clearance."
        )
    return result


def reverse_search(digest: str) -> dict:
    url = (os.environ.get("DEFENCE_STUDIO_REVERSE_IMAGE_URL") or "").strip()
    if not url:
        return {"available": False, "reason": "UNAVAILABLE_NOT_CONFIGURED", "matches": None, "query": digest}
    return {
        "available": False,
        "reason": "CONFIGURED_BUT_NOT_CALLED",
        "matches": None,
        "query": digest,
        "detail": "A URL is configured. This engine does not call it without an explicit live-search request.",
    }


def verify_media(data: bytes, claim: dict | None = None, *, known: list[dict] | None = None,
                 frames: list[dict] | None = None, filename: str = "upload",
                 registered_asset: dict | None = None, live_reverse_search: bool = False) -> dict:
    from defence_studio.verification import adapters
    claim = claim or {}
    known = known or []
    digest = sha256(data)
    kind = file_kind(data)
    text = png_text(data) if kind == "image/png" else {}
    exif_time = jpeg_exif_datetime(data) if kind == "image/jpeg" else None
    visual_hash = average_hash(data)
    exact = [row for row in known if row.get("sha256") == digest and row.get("asset_id") != claim.get("asset_id")]
    near = []
    for row in known:
        distance = hamming(visual_hash, row.get("average_hash"))
        if distance is not None and distance <= 8 and row.get("sha256") != digest:
            near.append({**row, "hamming": distance})
    signal = text.get("DefenceStudioTestSignal")
    manipulation = None
    if signal == "copied_region":
        manipulation = {
            "signal": signal,
            "source": "png-text-marker",
            "note": "A test or tool marker reported a copied region. Compression alone is not this signal.",
        }
    context, reasons = _context(claim, text, exif_time, exact, near, kind, frames)
    if manipulation and context == "INSUFFICIENT_EVIDENCE":
        context = "MANIPULATION_SUSPECTED"
    elif manipulation and context not in {"CONTEXT_MISMATCH", "RECYCLED_OR_OLD"}:
        reasons.append("A copied-region marker is present. That is separate from whether the event matches.")
    frame_findings = _frames(frames or [], claim)
    if kind.startswith("video/") and frames is None and not shutil.which("ffmpeg"):
        reasons.append("ffmpeg is not available, so no frames were sampled.")
        if context == "CONTEXT_MATCHED":
            context = "INSUFFICIENT_EVIDENCE"
    ocr = adapters.ocr_image(data) if kind.startswith("image/") else {
        "available": False, "status": "UNSUPPORTED", "text": None, "reason": "OCR runs on image uploads only."}
    return {
        "schema": "defence-studio.factcheck.media.v1",
        "filename": os.path.basename(filename).replace("\\", "/").split("/")[-1],
        "media_type": kind,
        "sha256": digest,
        "size_bytes": len(data),
        "average_hash": visual_hash,
        "embedded_text": text,
        "exif_datetime": exif_time,
        "exif_trust": "untrusted" if exif_time else "absent",
        "exact_duplicates": [{"asset_id": row.get("asset_id"), "event_date": row.get("event_date")} for row in exact],
        "near_duplicates": [{"asset_id": row.get("asset_id"), "event_date": row.get("event_date"), "hamming": row["hamming"]} for row in near],
        "context_status": context,
        "authenticity_status": "MANIPULATION_SUSPECTED" if manipulation else "NOT_ASSESSED",
        "manipulation": manipulation,
        "rights": rights_status(claim.get("asset_id"), registered_asset),
        "reverse_search": (adapters.reverse_image_lookup(data, digest, visual_hash)
                           if live_reverse_search else reverse_search(digest)),
        "ocr": ocr,
        "object_detection": adapters.detect_objects(data),
        "frames": frame_findings,
        "reasons": reasons,
        "claim_compared": {
            "event_date": claim.get("event_date"),
            "location": claim.get("location"),
            "subject": claim.get("subject"),
        },
    }


def _context(claim, text, exif_time, exact, near, kind, frames) -> tuple[str, list[str]]:
    reasons = []
    caption = " ".join(text.get(key, "") for key in ("Caption", "Description", "Comment"))
    location = (claim.get("location") or "").lower()
    subject = (claim.get("subject") or "").lower()
    event_date = claim.get("event_date") or ""
    if not claim:
        return "INSUFFICIENT_EVIDENCE", ["No claim context was supplied."]
    recycled = []
    for row in list(exact) + list(near):
        prior = row.get("event_date") or ""
        if prior and event_date and prior != event_date:
            recycled.append(prior)
    if recycled:
        reasons.append(f"The file matches an earlier asset dated {recycled[0]}.")
        return "RECYCLED_OR_OLD", reasons
    caption_l = caption.lower()
    if location and caption and location not in caption_l and subject and subject not in caption_l:
        reasons.append("Embedded caption does not mention the claimed location or subject.")
        return "CONTEXT_MISMATCH", reasons
    if event_date and caption and event_date not in caption and location and location not in caption_l:
        reasons.append("Embedded caption disagrees with the claimed date and location.")
        return "CONTEXT_MISMATCH", reasons
    if exif_time and event_date and event_date not in exif_time:
        reasons.append("EXIF datetime does not contain the claimed date. EXIF is not treated as proof.")
    if caption and ((not location or location in caption_l) and (not subject or subject in caption_l) and (not event_date or event_date in caption)):
        reasons.append("Embedded caption agrees with the supplied event context. That does not prove the event.")
        return "CONTEXT_MATCHED", reasons
    if caption and (location in caption_l or subject in caption_l or (event_date and event_date in caption)):
        reasons.append("Embedded text overlaps only part of the claimed context.")
        return "POSSIBLE_CONTEXT_MATCH", reasons
    if kind.startswith("video/") and not frames:
        reasons.append("No decoded frames were supplied.")
    elif not caption and not exif_time and visual_missing(kind):
        reasons.append("No caption, EXIF datetime, or comparable pixels were available.")
    else:
        reasons.append("The file has no embedded context that confirms the claimed event.")
    return "INSUFFICIENT_EVIDENCE", reasons


def visual_missing(kind: str) -> bool:
    return kind not in {"image/png", "image/x-portable-pixmap"}


def _frames(frames: list[dict], claim: dict) -> list[dict]:
    findings = []
    for frame in frames:
        raw = frame.get("bytes") or b""
        supplied = frame.get("timestamp_ms")
        valid = (isinstance(supplied, (int, float)) and not isinstance(supplied, bool)
                 and supplied == supplied and supplied != float("inf") and supplied >= 0)
        checked = verify_media(raw, claim, known=[], frames=[]) if raw else None
        findings.append({
            "timestamp_ms": supplied if valid else None,
            "sample_index": frame.get("sample_index"),
            "timestamp_origin": (frame.get("timestamp_origin") or "supplied_pts") if valid else "unverified",
            "sha256": sha256(raw) if raw else None,
            "context_status": None if checked is None else checked["context_status"],
            "note": (frame.get("note") or "Finding is tied to a supplied timestamp.") if valid else (
                "Timestamp was missing, malformed or negative and was discarded; the sample index is not a source timestamp."
                if supplied is not None else "The sample index is not a source timestamp."
            ),
        })
    return findings
