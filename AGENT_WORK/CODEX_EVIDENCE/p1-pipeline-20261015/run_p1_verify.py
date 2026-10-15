"""One-shot P1 English media pipeline verification. Evidence output only."""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"E:\Jana Soft\DefenceStudio")
MP4 = REPO / "AGENT_WORK/CODEX_EVIDENCE/master-readiness-20261015/real-module-subtitled.mp4"
EXPECTED_SHA = "914f576bb18575e023fe23ea01debf9c187ce1f198abe8ade07af1f531fda265"
OUT = Path(__file__).resolve().parent
FRAMES = OUT / "frames-live-20261015"
TTS = OUT / "tts-live-20261015"
TEXT = "The army ordered new jets last week."
PYTHON_CANDIDATES = [
    Path(r"C:\Users\Pharma\AppData\Local\Temp\defence-master-audio-20261015\Scripts\python.exe"),
    REPO / "standalone/audio-module/.venv/Scripts/python.exe",
    REPO / "standalone/audio-module/venv/Scripts/python.exe",
    REPO / "AGENT_WORK/CURSOR_EVIDENCE/modules-e2e/venv-tts/Scripts/python.exe",
]


def run(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def git_summary() -> dict:
    rc, out, err = run(["git", "-C", str(REPO), "status", "--short"])
    _, branch, _ = run(["git", "-C", str(REPO), "branch", "--show-current"])
    return {
        "branch": branch.strip(),
        "status_short": out.splitlines(),
        "exit_code": rc,
        "stderr": err.strip() or None,
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_json(path: Path) -> dict:
    rc, out, err = run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if rc != 0:
        return {"error": err or out, "exit_code": rc}
    return json.loads(out)


def speech_metrics(path: Path) -> dict:
    pcm = OUT / "narration_extract.pcm"
    rc, _, err = run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-f",
            "s16le",
            str(pcm),
        ]
    )
    if rc != 0:
        return {"extract_exit": rc, "error": err}
    data = pcm.read_bytes()
    n = len(data) // 2
    samples = struct.unpack(f"<{n}h", data[: n * 2])
    sr = 48000
    frame = int(0.25 * sr)
    thresh = 10 ** (-35 / 20.0)
    silent_frames = 0
    total_frames = max(1, math.ceil(n / frame))
    rms_vals = []
    silent_run = longest_silent_run = 0
    silent_runs = []
    for i in range(0, n, frame):
        chunk = samples[i : i + frame]
        if not chunk:
            continue
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0
        rms_vals.append(rms)
        if rms < thresh:
            silent_frames += 1
            silent_run += len(chunk)
        elif silent_run:
            silent_runs.append(silent_run / sr)
            longest_silent_run = max(longest_silent_run, silent_run)
            silent_run = 0
    if silent_run:
        silent_runs.append(silent_run / sr)
        longest_silent_run = max(longest_silent_run, silent_run)
    overall_rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples))) / 32768.0
    rms_db = 20 * math.log10(max(overall_rms, 1e-12))
    # energy variation: coefficient of variation on frame RMS (speech should vary)
    mean_r = sum(rms_vals) / max(1, len(rms_vals))
    var_r = sum((x - mean_r) ** 2 for x in rms_vals) / max(1, len(rms_vals))
    cv = (var_r**0.5 / mean_r) if mean_r > 1e-9 else 0.0
    return {
        "extract_exit": 0,
        "sample_rate": sr,
        "duration_s": n / sr,
        "overall_rms_linear": round(overall_rms, 6),
        "overall_rms_db": round(rms_db, 2),
        "silence_threshold_db": -35,
        "silence_frame_s": 0.25,
        "silent_frame_fraction": round(silent_frames / total_frames, 4),
        "silent_run_count": len(silent_runs),
        "longest_silent_run_s": round(longest_silent_run / sr, 4),
        "silent_runs_over_0_25s": sum(run > 0.25 for run in silent_runs),
        "frame_rms_cv": round(cv, 4),
        "energy_varies": cv > 0.15 and overall_rms > thresh,
    }


def extract_frames(path: Path) -> list[dict]:
    FRAMES.mkdir(parents=True, exist_ok=True)
    out = []
    for t in (1.0, 3.5, 6.5):
        fp = FRAMES / f"frame_{t:.1f}s.png"
        rc, _, err = run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(t),
                "-i",
                str(path),
                "-frames:v",
                "1",
                str(fp),
            ]
        )
        analysis = {"time_s": t, "path": str(fp), "extract_exit": rc}
        if rc == 0 and fp.is_file():
            try:
                from PIL import Image

                im = Image.open(fp).convert("L")
                w, h = im.size
                band = im.crop((0, int(h * 0.72), w, h))
                px = list(band.getdata())
                mean = sum(px) / len(px)
                std = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5
                dark = sum(1 for p in px if p < 80)
                bright = sum(1 for p in px if p > 200)
                analysis.update(
                    {
                        "lower_third_mean_luma": round(mean, 2),
                        "lower_third_std_luma": round(std, 2),
                        "dark_pixel_fraction": round(dark / len(px), 4),
                        "bright_pixel_fraction": round(bright / len(px), 4),
                        "glyph_like_contrast": std > 25 and bright > 50 and dark > 50,
                    }
                )
            except Exception as exc:
                analysis["pil_error"] = f"{type(exc).__name__}: {exc}"
        else:
            analysis["error"] = err
        out.append(analysis)
    return out


def pick_python() -> Path | None:
    for p in PYTHON_CANDIDATES:
        if p.is_file():
            return p
    return None


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def wav_rms(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
    n = len(frames) // 2
    samples = struct.unpack(f"<{n}h", frames)
    return math.sqrt(sum(s * s for s in samples) / max(1, n)) / 32768.0


def tts_controls(py: Path) -> dict:
    TTS.mkdir(parents=True, exist_ok=True)
    audio_mod = REPO / "standalone/audio-module"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(audio_mod)
    snippet = r"""
import json, sys, wave, math, struct
from pathlib import Path
sys.path.insert(0, r"{audio_mod}")
from app.tts.local_pyttsx3 import LocalPyttsx3Adapter
from app.tts.base import TTSRequest
from app.pipeline.measure import measure

text = {text!r}
out_dir = Path(r"{tts}")
adapter = LocalPyttsx3Adapter()

def dur(p):
    import wave
    with wave.open(str(p), 'rb') as w:
        return w.getnframes()/float(w.getframerate())

def rms(p):
    import wave, struct, math
    with wave.open(str(p), 'rb') as w:
        frames = w.readframes(w.getnframes())
    n = len(frames)//2
    samples = struct.unpack('<' + 'h'*n, frames)
    return math.sqrt(sum(s*s for s in samples)/max(1,n))/32768.0

def sample(p, result):
    measured = measure(p, text)
    measured.update({{'path': str(p), 'voice_id': result.voice,
                     'controls': result.provider_metadata['controls']}})
    return measured

results = {{}}
for rate in (110, 230):
    dest = out_dir / f'rate_{{rate}}.wav'
    result = adapter.synthesize(TTSRequest(text=text, language='en', voice_id=None, controls={{'rate_wpm': rate, 'volume': 0.85, 'pitch_semitones': 0}}), dest)
    results[f'rate_{{rate}}'] = sample(dest, result)

for vol in (0.25, 1.0):
    dest = out_dir / f'volume_{{vol}}.wav'
    result = adapter.synthesize(TTSRequest(text=text, language='en', voice_id=None, controls={{'rate_wpm': 170, 'volume': vol, 'pitch_semitones': 0}}), dest)
    results[f'volume_{{vol}}'] = sample(dest, result)

pitch = {{'code_present': True}}
try:
    import librosa  # noqa: F401
    import soundfile  # noqa: F401
    pitch['runtime_deps'] = 'ok'
    for ps in (0, 3):
        dest = out_dir / f'pitch_{{ps}}.wav'
        result = adapter.synthesize(TTSRequest(text=text, language='en', voice_id=None, controls={{'rate_wpm': 170, 'volume': 0.85, 'pitch_semitones': ps}}), dest)
        pitch[f'pitch_{{ps}}'] = sample(dest, result)
except ImportError as exc:
    pitch['runtime_deps'] = 'missing'
    pitch['import_error'] = str(exc)

print(json.dumps({{'rate_volume': results, 'pitch': pitch}}))
""".format(
        audio_mod=str(audio_mod).replace("\\", "\\\\"),
        tts=str(TTS).replace("\\", "\\\\"),
        text=TEXT,
    )
    rc, out, err = run([str(py), "-c", snippet], cwd=audio_mod)
    if rc != 0:
        return {"python": str(py), "exit_code": rc, "stderr": err, "stdout": out}
    return {"python": str(py), "measurements": json.loads(out.strip())}


def main() -> int:
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(REPO),
        "mp4_path": str(MP4),
        "commands_run": [],
    }
    report["git"] = git_summary()
    report["commands_run"].append("git status --short; git branch --show-current")

    if not MP4.is_file():
        report["mp4"] = {"present": False, "stop_reason": "missing file"}
        (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 2

    report["mp4"] = {"present": True}
    sha = sha256_file(MP4)
    report["mp4"]["sha256"] = sha
    report["mp4"]["expected_sha256"] = EXPECTED_SHA
    report["mp4"]["sha256_match"] = sha == EXPECTED_SHA
    report["mp4"]["bytes"] = MP4.stat().st_size
    report["commands_run"].append("sha256 real-module-subtitled.mp4")

    if sha != EXPECTED_SHA:
        (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 3

    rc, _, err = run(["ffmpeg", "-v", "error", "-i", str(MP4), "-f", "null", "-"])
    report["decode"] = {"exit_code": rc, "stderr": err.strip() or None}
    report["commands_run"].append("ffmpeg -f null full decode")

    probe = ffprobe_json(MP4)
    report["ffprobe"] = probe
    report["commands_run"].append("ffprobe json")
    if "streams" in probe:
        vd = next((s.get("duration") for s in probe["streams"] if s.get("codec_type") == "video"), None)
        ad = next((s.get("duration") for s in probe["streams"] if s.get("codec_type") == "audio"), None)
        if vd and ad:
            report["av_duration_delta_ms"] = round(abs(float(vd) - float(ad)) * 1000, 3)

    report["speech"] = speech_metrics(MP4)
    report["commands_run"].append("ffmpeg pcm extract + RMS/silence analysis")

    report["subtitle_frames"] = extract_frames(MP4)
    report["commands_run"].append("ffmpeg frame extract @ 1.0/3.5/6.5s")

    report["subtitle_timings"] = {
        "sidecar_next_to_mp4": False,
        "note": "Burned-in ASS only in MP4; prior integration test asserted cue_count >= scene count and |video-audio duration| < 0.25s",
        "narration_scenes": [
            "Here is what officials said.",
            "The army ordered new jets last week.",
            "That is the latest update.",
        ],
        "video_duration_s": float(probe.get("format", {}).get("duration", 0) or 0),
    }

    py = pick_python()
    if py:
        report["tts_controls"] = tts_controls(py)
        report["commands_run"].append(f"{py} LocalPyttsx3Adapter control WAVs")
    else:
        report["tts_controls"] = {"error": "no python candidate found"}

    report["pipeline_voice_controls_honesty"] = {
        "core_post_synthesize_fields": ["text", "provider", "language", "voice_id"],
        "voice_direction_applied_in_audio_service": False,
        "renderer_pause_after_ms": "mix-time silence in RenderService._concat_narration",
        "profile_pauses_emphasis": "unsupported via TTS controls",
        "profile_energy": "performance-plan mapping to volume only",
    }

    report["remaining_gaps"] = [
        "Tamil TTS blocked (font/TTC parsing verified elsewhere; not claimed working)",
        "Human episode sign-off",
    ]

    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = [
        "# P1 English pipeline verification",
        "",
        f"- MP4: `{MP4}`",
        f"- SHA256 match: {report['mp4']['sha256_match']}",
        f"- Decode exit: {report['decode']['exit_code']}",
        f"- Git branch: {report['git'].get('branch')}",
        "",
        "See `report.json` for speech metrics, frame analysis, and TTS control measurements.",
    ]
    (OUT / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    speech_ok = report.get("speech", {}).get("extract_exit") == 0
    controls_ok = "measurements" in report.get("tts_controls", {})
    return 0 if report["decode"]["exit_code"] == 0 and speech_ok and controls_ok else 1


if __name__ == "__main__":
    sys.exit(main())
