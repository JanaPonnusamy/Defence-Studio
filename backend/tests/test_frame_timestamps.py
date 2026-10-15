"""Frame timestamp safety: unit tests with synthetic frames, mocked-ffmpeg association, plus real local ffmpeg."""
import shutil
import subprocess

import pytest

from defence_studio.verification import media
from defence_studio.verification.media import sample_video_frames, verify_media

VIDEO = b"\x00\x00\x00\x18ftypisom"


def frames_for(items):
    return verify_media(VIDEO, {"subject": "clip"}, frames=items)["frames"]


@pytest.mark.parametrize("bad", [None, "1500", -1, -0.5, float("nan"), float("inf"), True, [1], {}])
def test_missing_malformed_negative_become_unverified(bad):
    f = frames_for([{"timestamp_ms": bad, "sample_index": 7, "bytes": b"\x00" * 8}])[0]
    assert f["timestamp_ms"] is None and f["timestamp_origin"] == "unverified"
    assert f["sample_index"] == 7 and "not a source timestamp" in f["note"]


def test_supplied_pts_preserved_with_precision_and_origin():
    f = frames_for([{"timestamp_ms": 1500, "sample_index": 0, "bytes": b"\x00" * 8},
                    {"timestamp_ms": 0, "sample_index": 1, "bytes": b"\x01" * 8},
                    {"timestamp_ms": 2500.5, "sample_index": 2, "bytes": b"\x02" * 8}])
    assert [x["timestamp_ms"] for x in f] == [1500, 0, 2500.5]
    assert all(x["timestamp_origin"] == "supplied_pts" for x in f)


def test_each_finding_stays_tied_to_its_own_frame():
    items = [{"timestamp_ms": t, "sample_index": i, "bytes": bytes([i + 1]) * 8}
             for i, t in enumerate((100, None, 300))]
    f = frames_for(items)
    assert [x["sample_index"] for x in f] == [0, 1, 2]
    assert [x["timestamp_ms"] for x in f] == [100, None, 300]
    assert len({x["sha256"] for x in f}) == 3


def test_explicit_origin_kept_only_when_time_is_valid():
    ok = frames_for([{"timestamp_ms": 5, "timestamp_origin": "ffmpeg_showinfo_pts_time", "bytes": b"a" * 8}])[0]
    bad = frames_for([{"timestamp_ms": -5, "timestamp_origin": "ffmpeg_showinfo_pts_time", "bytes": b"a" * 8}])[0]
    assert ok["timestamp_origin"] == "ffmpeg_showinfo_pts_time" and bad["timestamp_origin"] == "unverified"


def _fake_ffmpeg(monkeypatch, log, frame_count):
    class Done:
        returncode, stderr = 0, log.encode()

    def fake_run(cmd, **kw):
        for i in range(1, frame_count + 1):
            with open(cmd[-1].replace("%02d", f"{i:02d}"), "wb") as fh:
                fh.write(bytes([i]) * 4)
        return Done()
    monkeypatch.setattr(media.shutil, "which", lambda n: "ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)


def test_showinfo_association_uses_frame_number_not_position(monkeypatch, tmp_path):
    """MOCKED ffmpeg: frame 1 prints no pts_time; frame 2 must keep its own time."""
    log = ("[Parsed_showinfo_1] n:   0 pts:  0 pts_time:0 pos: 1\n"
           "[Parsed_showinfo_1] n:   1 pts: NOPTS pts_time:NOPTS pos: 2\n"
           "[Parsed_showinfo_1] n:   2 pts: 2000 pts_time:2.25 pos: 3\n")
    _fake_ffmpeg(monkeypatch, log, 3)
    r = sample_video_frames(str(tmp_path / "x.mp4"))
    assert [f["timestamp_ms"] for f in r["frames"]] == [0, None, 2250]
    assert [f["timestamp_origin"] for f in r["frames"]] == [
        "ffmpeg_showinfo_pts_time", "unverified", "ffmpeg_showinfo_pts_time"]
    assert [f["sample_index"] for f in r["frames"]] == [0, 1, 2]


def test_negative_pts_discarded(monkeypatch, tmp_path):
    _fake_ffmpeg(monkeypatch, "[Parsed_showinfo_1] n:   0 pts: -512 pts_time:-0.033 pos: 1\n", 1)
    f = sample_video_frames(str(tmp_path / "x.mp4"))["frames"][0]
    assert f["timestamp_ms"] is None and f["timestamp_origin"] == "unverified"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_real_ffmpeg_frames_have_measured_pts(tmp_path):
    """Real local processing (no mocks)."""
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=4:s=64x64:r=10",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    ts = [f["timestamp_ms"] for f in sample_video_frames(str(clip))["frames"]]
    assert len(ts) == 3 and ts == sorted(ts) and ts[0] == 0 and ts[1] >= 900
