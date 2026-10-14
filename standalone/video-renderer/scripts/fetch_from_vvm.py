"""Build a video-render-package from a RUNNING Video Visual Module using only its public GET endpoints.

    GET /visual-plans/{id}/timeline     -> timeline.json (stored byte-for-byte)
    GET /assets/{asset_id}/file         -> assets/<asset_id>.<ext>   (sha256 checked against the timeline)

plus a user-supplied narration WAV. No VVM code is imported; only localhost HTTP is used.

    python scripts/fetch_from_vvm.py --vvm http://127.0.0.1:8765 --plan <visual_plan_id> ^
        --narration narration.wav --out storage\\packages\\episode1 [--width 1920 --height 1080 --fps 25]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renderer.package import PackageInvalid, load_package, write_manifest  # noqa: E402

EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _is_local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def http_get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - scheme/host checked by caller
        return r.read()


def fetch(vvm: str, plan_id: str, narration: Path, out: Path, profile: dict | None, allow_remote: bool = False) -> Path:
    vvm = vvm.rstrip("/")
    if not urllib.parse.urlparse(vvm).scheme.startswith("http"):
        raise SystemExit("--vvm must be an http(s) URL")
    if not allow_remote and not _is_local(vvm):
        raise SystemExit("refusing non-localhost VVM URL (offline tool); pass --allow-remote to override")
    if not narration.is_file():
        raise SystemExit(f"narration file not found: {narration}")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"output folder {out} is not empty; refusing to overwrite")
    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    try:
        tl_bytes = http_get(f"{vvm}/visual-plans/{urllib.parse.quote(plan_id)}/timeline")
        timeline = json.loads(tl_bytes)
        (out / "timeline.json").write_bytes(tl_bytes)
        shutil.copyfile(narration, out / "narration.wav")
        needed = sorted({s["asset_id"] for s in timeline["shots"] if s.get("status") == "READY" and s.get("asset_id")})
        assets: dict[str, tuple[str, str]] = {}
        for aid in needed:
            ref = timeline["assets"][aid]
            mime = ref.get("mime_type")
            if mime not in EXT:
                raise SystemExit(f"asset {aid}: mime type {mime!r} is not a supported still image in R1")
            data = http_get(f"{vvm}/assets/{urllib.parse.quote(aid)}/file")
            if ref.get("checksum_sha256") and hashlib.sha256(data).hexdigest() != ref["checksum_sha256"]:
                raise SystemExit(f"asset {aid}: downloaded bytes do not match the timeline's checksum_sha256")
            rel = f"assets/{_SAFE.sub('_', aid)}.{EXT[mime]}"
            (out / rel).write_bytes(data)
            assets[aid] = (rel, mime)
        write_manifest(out, timeline_rel="timeline.json", narration_rel="narration.wav", assets=assets,
                       output_profile=profile,
                       source={"vvm": vvm, "visual_plan_id": plan_id, "fetched_with": "scripts/fetch_from_vvm.py"})
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as exc:
        shutil.rmtree(out, ignore_errors=True)
        raise SystemExit(f"fetch failed: {type(exc).__name__}: {exc}") from None
    except SystemExit:
        shutil.rmtree(out, ignore_errors=True)
        raise
    try:
        pkg = load_package(out)
    except PackageInvalid as exc:
        print("PACKAGE INVALID (kept for inspection):", *exc.details, sep="\n  - ")
        raise SystemExit(2) from None
    print(f"package OK: {out}  shots={len(pkg.timeline['shots'])} assets={len(pkg.assets)} "
          f"narration={pkg.narration.duration:.2f}s profile={pkg.profile.as_dict()}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vvm", default="http://127.0.0.1:8765")
    ap.add_argument("--plan", required=True, help="visual_plan_id (a BUILT plan)")
    ap.add_argument("--narration", required=True, type=Path, help="narration WAV supplied by you")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=float)
    ap.add_argument("--allow-remote", action="store_true")
    a = ap.parse_args()
    given = [a.width, a.height, a.fps]
    if any(v is not None for v in given) and not all(v is not None for v in given):
        raise SystemExit("--width, --height and --fps must be given together")
    profile = {"width": a.width, "height": a.height, "fps": a.fps} if a.width else None
    fetch(a.vvm, a.plan, a.narration, a.out, profile, a.allow_remote)


if __name__ == "__main__":
    main()
