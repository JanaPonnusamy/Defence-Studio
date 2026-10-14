"""The "video-render-package" v1 contract and its validator (docs/CONTRACTS.md, schemas/).

A package is a folder:

    package.json        manifest (this contract)
    timeline.json       Video Visual timeline (video-visual-timeline 1.0/1.1), byte-exact export
    narration.wav       narration audio (PCM / IEEE-float WAV); defines the video duration
    assets/<file>       one file per asset_id referenced by READY shots

Everything is checked before a render starts: manifest shape, every path (no traversal, no
absolute paths, must stay inside the package folder), the sha256 of every file, image
decodability and MIME type, the timeline rules and that every READY shot resolves to a file.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from PIL import Image

from renderer.config import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH
from renderer.errors import PackageInvalid
from renderer.timeline_rules import validate_timeline
from renderer.wavinfo import WavError, WavInfo, read_wav_info

PACKAGE_SCHEMA_ID = "video-render-package"
PACKAGE_VERSION = "1"
MANIFEST_NAME = "package.json"
SUPPORTED_IMAGE_MIME = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TIMELINE_BYTES = 64 * 1024 * 1024
MAX_ASSETS = 5000
MAX_ASSET_BYTES = 256 * 1024 * 1024
MAX_IMAGE_PIXELS = 120_000_000
MIN_DIM, MAX_W, MAX_H, MAX_FPS = 16, 7680, 4320, 60.0
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


@dataclass(frozen=True)
class OutputProfile:
    width: int
    height: int
    fps: float
    source: str  # "package" | "timeline" | "default"

    def as_dict(self) -> dict:
        return {"width": self.width, "height": self.height, "fps": self.fps, "source": self.source}


@dataclass(frozen=True)
class AssetFile:
    asset_id: str
    path: Path
    sha256: str
    mime_type: str
    width: int
    height: int


@dataclass
class RenderPackage:
    root: Path
    manifest: dict
    manifest_sha256: str
    timeline: dict
    narration_path: Path
    narration: WavInfo
    assets: dict[str, AssetFile]
    profile: OutputProfile
    warnings: list[dict] = field(default_factory=list)


def safe_member_path(root: Path, rel, what: str, problems: list[str]) -> Path | None:
    """Resolve a manifest path inside `root`; record a problem and return None if unsafe."""
    if not isinstance(rel, str) or not rel.strip():
        problems.append(f"{what}: path must be a non-empty string")
        return None
    if "\\" in rel or "\x00" in rel:
        problems.append(f"{what}: path '{rel}' must use '/' separators and no NUL bytes")
        return None
    pp, wp = PurePosixPath(rel), PureWindowsPath(rel)
    if pp.is_absolute() or wp.is_absolute() or wp.drive or rel.startswith("/"):
        problems.append(f"{what}: absolute path '{rel}' is not allowed")
        return None
    if any(part in ("..", "") for part in rel.split("/")) or ":" in rel:
        problems.append(f"{what}: path '{rel}' escapes or is malformed (path traversal)")
        return None
    root_r = root.resolve()
    full = (root_r / rel).resolve()
    if not full.is_relative_to(root_r):
        problems.append(f"{what}: path '{rel}' resolves outside the package (path traversal)")
        return None
    if not full.is_file():
        problems.append(f"{what}: file '{rel}' does not exist")
        return None
    return full


def _check_sha(path: Path, expected, what: str, problems: list[str]) -> str | None:
    if not isinstance(expected, str) or not _SHA_RE.match(expected):
        problems.append(f"{what}: sha256 must be 64 lowercase hex characters")
        return None
    actual = sha256_file(path)
    if actual != expected:
        problems.append(f"{what}: sha256 mismatch (manifest {expected[:12]}..., file {actual[:12]}...)")
        return None
    return actual


def _resolve_profile(manifest: dict, timeline: dict, problems: list[str]) -> OutputProfile | None:
    op = manifest.get("output_profile")
    if op is not None:
        if not isinstance(op, dict) or set(op) - {"width", "height", "fps"} or \
                not all(k in op for k in ("width", "height", "fps")):
            problems.append("output_profile must be {width, height, fps} or null")
            return None
        w, h, fps, source = op["width"], op["height"], op["fps"], "package"
    else:
        out = timeline.get("output") or {}
        if out.get("width") and out.get("height") and out.get("frame_rate"):
            w, h, fps, source = out["width"], out["height"], out["frame_rate"], "timeline"
        else:
            w, h, fps, source = DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_FPS, "default"
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (w, h)) or \
            not (MIN_DIM <= w <= MAX_W and MIN_DIM <= h <= MAX_H) or w % 2 or h % 2:
        problems.append(f"output size {w}x{h} invalid (even integers, {MIN_DIM}..{MAX_W}x{MIN_DIM}..{MAX_H})")
        return None
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not (1.0 <= fps <= MAX_FPS):
        problems.append(f"output fps {fps!r} invalid (1..{MAX_FPS:g})")
        return None
    return OutputProfile(w, h, float(fps), source)


def load_package(root: Path) -> RenderPackage:
    """Validate a package folder. Raises PackageInvalid with every problem found."""
    root = Path(root)
    problems: list[str] = []
    mpath = root / MANIFEST_NAME
    if not root.is_dir() or not mpath.is_file():
        raise PackageInvalid([f"no {MANIFEST_NAME} in package folder '{root}'"])
    if mpath.stat().st_size > MAX_MANIFEST_BYTES:
        raise PackageInvalid([f"{MANIFEST_NAME} is larger than {MAX_MANIFEST_BYTES} bytes"])
    raw = mpath.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))  # tolerate a BOM from Windows editors/PowerShell
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageInvalid([f"{MANIFEST_NAME} is not valid UTF-8 JSON ({type(exc).__name__})"]) from None
    if not isinstance(manifest, dict):
        raise PackageInvalid([f"{MANIFEST_NAME} must be a JSON object"])
    if manifest.get("schema") != PACKAGE_SCHEMA_ID or str(manifest.get("version")) != PACKAGE_VERSION:
        raise PackageInvalid([f"schema/version must be '{PACKAGE_SCHEMA_ID}' / '{PACKAGE_VERSION}'"])
    allowed = {"schema", "version", "timeline", "timeline_sha256", "narration", "assets", "output_profile",
               "created_at", "source", "notes"}
    extra = set(manifest) - allowed
    if extra:
        problems.append(f"unknown manifest keys {sorted(extra)}")
    for k in ("timeline", "timeline_sha256", "narration", "assets"):
        if k not in manifest:
            problems.append(f"manifest missing '{k}'")
    if problems:
        raise PackageInvalid(problems)

    # ---- timeline
    timeline: dict = {}
    tpath = safe_member_path(root, manifest["timeline"], "timeline", problems)
    if tpath is not None:
        if tpath.stat().st_size > MAX_TIMELINE_BYTES:
            problems.append("timeline: file too large")
        elif _check_sha(tpath, manifest["timeline_sha256"], "timeline", problems):
            try:
                timeline = json.loads(tpath.read_text(encoding="utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                problems.append("timeline: not valid UTF-8 JSON")
            else:
                problems += [f"timeline: {e}" for e in validate_timeline(timeline)]

    # ---- narration
    narration_path, wav = None, None
    nar = manifest["narration"]
    if not isinstance(nar, dict) or set(nar) - {"path", "sha256"} or "path" not in nar or "sha256" not in nar:
        problems.append("narration must be {path, sha256}")
    else:
        narration_path = safe_member_path(root, nar["path"], "narration", problems)
        if narration_path is not None and _check_sha(narration_path, nar["sha256"], "narration", problems):
            try:
                wav = read_wav_info(narration_path)
                if wav.duration <= 0.1:
                    problems.append("narration: audio is empty or shorter than 0.1 s")
            except (WavError, OSError, ValueError) as exc:
                problems.append(f"narration: {exc}")

    # ---- assets
    assets: dict[str, AssetFile] = {}
    amap = manifest["assets"]
    if not isinstance(amap, dict):
        problems.append("assets must be an object keyed by asset_id")
        amap = {}
    if len(amap) > MAX_ASSETS:
        problems.append(f"too many assets ({len(amap)} > {MAX_ASSETS})")
        amap = {}
    tl_assets = timeline.get("assets") if isinstance(timeline.get("assets"), dict) else {}
    for aid, entry in amap.items():
        what = f"asset '{aid}'"
        if not isinstance(entry, dict) or set(entry) - {"path", "sha256", "mime_type"} or \
                not all(k in entry for k in ("path", "sha256", "mime_type")):
            problems.append(f"{what}: must be {{path, sha256, mime_type}}")
            continue
        mime = entry["mime_type"]
        if mime not in SUPPORTED_IMAGE_MIME:
            problems.append(f"{what}: mime_type '{mime}' not supported in R1 (still images only: "
                            f"{sorted(SUPPORTED_IMAGE_MIME)})")
            continue
        apath = safe_member_path(root, entry["path"], what, problems)
        if apath is None:
            continue
        if apath.stat().st_size > MAX_ASSET_BYTES:
            problems.append(f"{what}: file too large")
            continue
        sha = _check_sha(apath, entry["sha256"], what, problems)
        if sha is None:
            continue
        ref_sha = (tl_assets.get(aid) or {}).get("checksum_sha256")
        if ref_sha and ref_sha != sha:
            problems.append(f"{what}: file does not match the timeline's checksum_sha256")
            continue
        try:
            with Image.open(apath) as im:
                fmt, (w, h) = im.format, im.size
                if w * h > MAX_IMAGE_PIXELS:
                    raise ValueError(f"image too large ({w}x{h})")
                im.verify()
        except Exception as exc:  # noqa: BLE001 - any decoder failure means an unusable asset
            problems.append(f"{what}: image cannot be decoded ({type(exc).__name__}: {exc})")
            continue
        if fmt != SUPPORTED_IMAGE_MIME[mime]:
            problems.append(f"{what}: content is {fmt}, manifest says {mime}")
            continue
        assets[aid] = AssetFile(aid, apath, sha, mime, w, h)

    # ---- every READY shot must resolve to a validated asset file
    warnings: list[dict] = []
    if isinstance(timeline.get("shots"), list):
        for i, s in enumerate(timeline["shots"]):
            if isinstance(s, dict) and s.get("status") == "READY" and s.get("asset_id"):
                aid = s["asset_id"]
                if aid not in amap:
                    problems.append(f"shot {i} READY asset '{aid}' is not in the package")
                elif aid not in assets:
                    pass  # already reported above
        referenced = {s.get("asset_id") for s in timeline["shots"] if isinstance(s, dict)}
        unused = sorted(set(amap) - referenced)
        if unused:
            shown = ", ".join(unused[:5]) + (" ..." if len(unused) > 5 else "")
            warnings.append({"code": "UNUSED_ASSET", "count": len(unused),
                             "message": f"{len(unused)} asset(s) not used by any shot: {shown}"})

    profile = _resolve_profile(manifest, timeline, problems) if timeline else None
    if problems:
        raise PackageInvalid(problems)
    return RenderPackage(root=root.resolve(), manifest=manifest, manifest_sha256=hashlib.sha256(raw).hexdigest(),
                         timeline=timeline, narration_path=narration_path, narration=wav, assets=assets,
                         profile=profile, warnings=warnings)


def write_manifest(root: Path, *, timeline_rel: str, narration_rel: str, assets: dict[str, tuple[str, str]],
                   output_profile: dict | None = None, source: dict | None = None) -> dict:
    """Build package.json for files already inside `root`. `assets` maps asset_id -> (rel_path, mime)."""
    root = Path(root)
    manifest = {
        "schema": PACKAGE_SCHEMA_ID,
        "version": PACKAGE_VERSION,
        "timeline": timeline_rel,
        "timeline_sha256": sha256_file(root / timeline_rel),
        "narration": {"path": narration_rel, "sha256": sha256_file(root / narration_rel)},
        "assets": {aid: {"path": rel, "sha256": sha256_file(root / rel), "mime_type": mime}
                   for aid, (rel, mime) in sorted(assets.items())},
        "output_profile": output_profile,
    }
    if source:
        manifest["source"] = source
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
