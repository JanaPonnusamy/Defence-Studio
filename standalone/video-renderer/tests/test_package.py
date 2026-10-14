from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from renderer.errors import PackageInvalid
from renderer.package import load_package, sha256_file
from renderer.synthetic import build_package
from tests.conftest import rewrite_manifest, rewrite_timeline

SCHEMA = json.loads((Path(__file__).parent.parent / "schemas" / "video-render-package.v1.schema.json").read_text())


def problems(root) -> list[str]:
    with pytest.raises(PackageInvalid) as e:
        load_package(root)
    return e.value.details


def test_valid_package_loads(pkg_dir):
    pkg = load_package(pkg_dir)
    assert len(pkg.timeline["shots"]) == 4 and len(pkg.assets) == 6
    assert pkg.narration.duration == pytest.approx(20.0, abs=0.01)
    assert (pkg.profile.width, pkg.profile.height, pkg.profile.fps, pkg.profile.source) == (320, 180, 12.0, "package")
    assert any(w["code"] == "UNUSED_ASSET" for w in pkg.warnings)  # 6 assets, 3 used


def test_manifest_matches_published_json_schema(pkg_dir):
    jsonschema.validate(json.loads((pkg_dir / "package.json").read_text()), SCHEMA)


@pytest.mark.parametrize("bad", [{"schema": "other"}, {"version": "2"}])
def test_bad_schema_or_version(pkg_dir, bad):
    rewrite_manifest(pkg_dir, lambda m: m.update(bad))
    assert "schema/version" in problems(pkg_dir)[0]


def test_unknown_manifest_key_rejected(pkg_dir):
    rewrite_manifest(pkg_dir, lambda m: m.update(surprise=1))
    assert any("unknown manifest keys" in p for p in problems(pkg_dir))


def test_missing_manifest_and_garbage_manifest(tmp_path):
    assert "no package.json" in problems(tmp_path)[0]
    (tmp_path / "package.json").write_bytes(b"\xff\xfe not json")
    assert "not valid UTF-8 JSON" in problems(tmp_path)[0]


def test_bad_sha256_everywhere(pkg_dir):
    def corrupt(m):
        m["timeline_sha256"] = "0" * 64
        m["narration"]["sha256"] = "1" * 64
        m["assets"]["asset-000"]["sha256"] = "2" * 64
    rewrite_manifest(pkg_dir, corrupt)
    ps = " | ".join(problems(pkg_dir))
    assert "timeline: sha256 mismatch" in ps
    assert "narration: sha256 mismatch" in ps
    assert "asset 'asset-000': sha256 mismatch" in ps


def test_malformed_sha256_rejected(pkg_dir):
    rewrite_manifest(pkg_dir, lambda m: m["narration"].update(sha256="ABC"))
    assert any("64 lowercase hex" in p for p in problems(pkg_dir))


def test_tampered_asset_file_detected(pkg_dir):
    (pkg_dir / "assets" / "asset-000.png").write_bytes(b"not the original bytes")
    assert any("sha256 mismatch" in p for p in problems(pkg_dir))


@pytest.mark.parametrize("evil", ["../outside.png", "assets/../../outside.png", "/etc/passwd", "C:/Windows/win.ini",
                                  "assets\\asset-000.png", "..", "assets//asset-000.png", "a:b.png"])
def test_path_traversal_and_unsafe_paths(pkg_dir, evil):
    rewrite_manifest(pkg_dir, lambda m: m["assets"]["asset-000"].update(path=evil))
    ps = problems(pkg_dir)
    assert any("asset 'asset-000'" in p and any(k in p for k in ("traversal", "absolute", "separators", "malformed"))
               for p in ps), (evil, ps)


def test_traversal_to_a_real_file_outside_the_package(pkg_dir, tmp_path):
    outside = tmp_path / "secret.png"
    shutil.copy(pkg_dir / "assets" / "asset-000.png", outside)
    rewrite_manifest(pkg_dir, lambda m: m["assets"]["asset-000"].update(path="../secret.png",
                                                                        sha256=sha256_file(outside)))
    assert any("traversal" in p for p in problems(pkg_dir))


def test_symlink_escape_rejected(pkg_dir, tmp_path):
    outside = tmp_path / "secret.png"
    shutil.copy(pkg_dir / "assets" / "asset-000.png", outside)
    link = pkg_dir / "assets" / "link.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")
    rewrite_manifest(pkg_dir, lambda m: m["assets"]["asset-000"].update(path="assets/link.png"))
    assert any("outside the package" in p for p in problems(pkg_dir))


def test_missing_asset_file(pkg_dir):
    (pkg_dir / "assets" / "asset-000.png").unlink()
    assert any("does not exist" in p for p in problems(pkg_dir))


def test_ready_shot_asset_not_in_manifest(pkg_dir):
    rewrite_manifest(pkg_dir, lambda m: m["assets"].pop("asset-000"))
    assert any("READY asset 'asset-000' is not in the package" in p for p in problems(pkg_dir))


def test_ready_shot_without_asset_in_timeline(pkg_dir):
    rewrite_timeline(pkg_dir, lambda t: t["shots"][0].update(asset_id=None))
    assert any("READY but asset reference missing" in p for p in problems(pkg_dir))


def test_gap_shot_is_valid_and_gap_with_asset_is_not(pkg_dir):
    pkg = load_package(pkg_dir)
    assert [s["status"] for s in pkg.timeline["shots"]] == ["READY", "READY", "GAP", "READY"]
    rewrite_timeline(pkg_dir, lambda t: t["shots"][2].update(asset_id="asset-000"))
    assert any("GAP must have null asset" in p for p in problems(pkg_dir))


def test_timeline_rules_enforced_inside_package(pkg_dir):
    rewrite_timeline(pkg_dir, lambda t: t["shots"][1].update(start=9.0, end=14.0))
    assert any("leaves an unplanned gap" in p or "overlaps" in p for p in problems(pkg_dir))


def test_unsupported_mime(pkg_dir):
    rewrite_manifest(pkg_dir, lambda m: m["assets"]["asset-000"].update(mime_type="video/mp4"))
    assert any("not supported in R1" in p for p in problems(pkg_dir))


def test_declared_mime_must_match_content(pkg_dir):
    rewrite_manifest(pkg_dir, lambda m: m["assets"]["asset-000"].update(mime_type="image/jpeg"))
    assert any("content is PNG" in p for p in problems(pkg_dir))


def test_undecodable_image(pkg_dir):
    bad = pkg_dir / "assets" / "asset-000.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\nbroken")
    sha = sha256_file(bad)
    rewrite_timeline(pkg_dir, lambda t: t["assets"]["asset-000"].update(checksum_sha256=sha))
    rewrite_manifest(pkg_dir, lambda m: m["assets"]["asset-000"].update(sha256=sha))
    assert any("cannot be decoded" in p for p in problems(pkg_dir))


def test_wav_must_be_a_wav(pkg_dir):
    w = pkg_dir / "narration.wav"
    w.write_bytes(b"ID3 this is not a wav")
    rewrite_manifest(pkg_dir, lambda m: m["narration"].update(sha256=sha256_file(w)))
    assert any("narration:" in p and "RIFF" in p for p in problems(pkg_dir))


def test_defaults_when_timeline_output_is_null(tmp_path):
    root = build_package(tmp_path / "p", durations=[2.0, 2.0], n_assets=2, profile=None)
    pkg = load_package(root)
    assert (pkg.profile.width, pkg.profile.height, pkg.profile.fps, pkg.profile.source) == (1920, 1080, 25.0, "default")


def test_timeline_output_used_when_no_profile(tmp_path):
    root = build_package(tmp_path / "p", durations=[2.0, 2.0], n_assets=2, profile=None,
                         timeline_output={"width": 1280, "height": 720, "frame_rate": 30})
    pkg = load_package(root)
    assert (pkg.profile.width, pkg.profile.height, pkg.profile.fps, pkg.profile.source) == (1280, 720, 30.0, "timeline")


@pytest.mark.parametrize("prof", [{"width": 1921, "height": 1080, "fps": 25}, {"width": 1920, "height": 1080, "fps": 0},
                                  {"width": 8, "height": 1080, "fps": 25}, {"width": 1920, "height": 1080},
                                  {"width": 1920, "height": 1080, "fps": 25, "x": 1}])
def test_invalid_output_profile(tmp_path, prof):
    root = build_package(tmp_path / "p", durations=[2.0], n_assets=1)
    rewrite_manifest(root, lambda m: m.update(output_profile=prof))
    assert any("output" in p for p in problems(root))


def test_timeline_checksum_must_match_asset_bytes(pkg_dir):
    def bump(t):
        t["assets"]["asset-000"]["checksum_sha256"] = "f" * 64
    rewrite_timeline(pkg_dir, bump)
    assert any("does not match the timeline's checksum_sha256" in p for p in problems(pkg_dir))


def test_utf8_bom_in_manifest_and_timeline_is_accepted(pkg_dir):
    """Windows PowerShell 5.1 (Set-Content -Encoding UTF8) writes a BOM; that must not break a package."""
    for name in ("timeline.json", "package.json"):
        p = pkg_dir / name
        p.write_bytes(bytes([0xEF, 0xBB, 0xBF]) + p.read_bytes())
        if name == "timeline.json":
            rewrite_manifest(pkg_dir, lambda m: m.__setitem__("timeline_sha256", sha256_file(p)))
    assert len(load_package(pkg_dir).timeline["shots"]) == 4
