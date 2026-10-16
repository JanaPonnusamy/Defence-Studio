"""RM-5: audio set + measured visual timeline -> render package -> MP4 (+ subtitles).

Pipeline (each step is recorded on the persistent ``render`` row):
1. verify inputs: current scene plan, SUCCEEDED non-mock audio set whose files
   re-hash correctly, and a visual timeline built from *that* audio's measured
   timing;
2. build a ``video-render-package`` v1 folder (narration = concatenated scene
   audio incl. requested pauses; timeline verbatim; READY-shot assets fetched
   from the Visual Module and checksum-verified);
3. render with the standalone Renderer over its public API (it re-validates the
   package, encodes, probes and fully decodes);
4. subtitles: cues derived from measured scene spans, exported as SRT/VTT/ASS
   and burned in with FFmpeg/libass (correct Tamil shaping with a Tamil font);
   the result is re-probed and fully decoded.
Outputs are content-addressed and never overwritten.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import wave
from pathlib import Path

from defence_studio.integrations import subtitles as subs
from defence_studio.integrations.renderer_module import RendererClient, RendererError
from defence_studio.integrations.visual_module import VisualModuleClient, VisualModuleError
from defence_studio.services.integration import IntegrationService, IntegrationStale, IntegrationValidationError
from defence_studio.services.script import _now

RENDER_KIND = "render"
RENDER_SCHEMA = "defence-studio.render.v1"
PACKAGE_SCHEMA = "defence-studio.render-package-ref.v1"
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class RenderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class RenderService(IntegrationService):
    def __init__(self, database_file, storage_root, *, provider=None, renderer_client=None, visual_client=None):
        super().__init__(database_file, storage_root, provider=provider)
        self._renderer_client, self._visual_client = renderer_client, visual_client

    def _renderer(self) -> RendererClient:
        return self._renderer_client or RendererClient()

    def _visual(self) -> VisualModuleClient:
        return self._visual_client or VisualModuleClient()

    # -- start ----------------------------------------------------------------

    def start_render(self, job_id: str, owner_id: str, language: str, idempotency_key: str, *,
                     audio_version: int, visual_version: int, burn_subtitles: bool = True,
                     background: bool = True) -> dict:
        plan_row, plan = self._approved_plan(job_id, owner_id, language)
        audio_row = self.get_integration(job_id, owner_id, language, "audio_set", audio_version)
        visual_row = self.get_integration(job_id, owner_id, language, "visual_timeline", visual_version)
        for label, row in (("audio", audio_row), ("visual timeline", visual_row)):
            if row["status"] != "SUCCEEDED":
                raise IntegrationStale(f"The {label} is {row['status']}, not SUCCEEDED")
            if row["input_binding"].get("scene_plan_sha256") != plan_row["plan_sha256"]:
                raise IntegrationStale(f"The {label} was built for a different scene plan")
        vb = visual_row["input_binding"]
        self.verify_visual_asset_rights(job_id, language, vb)
        if vb.get("timing") != "measured_audio" or vb.get("audio_set_sha256") != audio_row["sha256"]:
            raise IntegrationStale("The visual timeline was not built from this audio's measured timing; rebuild it "
                                   "with audio_version set")
        audio = self.read_integration_body(job_id, owner_id, language, "audio_set", audio_version)
        if audio["summary"]["any_mock"]:
            raise IntegrationValidationError("Mock audio is never accepted for rendering")
        verified = self.verify_audio_files(job_id, audio)
        if verified:
            raise IntegrationStale("Stored audio failed verification: " + "; ".join(verified))
        timeline_body = self.read_integration_body(job_id, owner_id, language, "visual_timeline", visual_version)
        if language in ("ta",):
            subs.resolve_font(language)  # fail now, not after a long render
        binding = {"scene_plan_sha256": plan_row["plan_sha256"], "audio_version": audio_version,
                   "audio_set_sha256": audio_row["sha256"], "visual_version": visual_version,
                   "visual_sha256": visual_row["sha256"], "burn_subtitles": bool(burn_subtitles),
                   "output": vb["options"], "language": language}
        binding["assets"] = vb.get("assets", [])
        item, created = self._create_row(job_id, owner_id, language, RENDER_KIND, binding, idempotency_key)
        if created:
            self._launch(lambda: self._run(job_id, language, item["version"], binding, audio, timeline_body),
                         background)
        return self.get_integration(job_id, owner_id, language, RENDER_KIND, item["version"])

    def retry_render(self, job_id: str, owner_id: str, language: str, version: int, idempotency_key: str, *,
                     background: bool = True) -> dict:
        old = self.get_integration(job_id, owner_id, language, RENDER_KIND, version)
        if old["status"] not in ("FAILED", "INTERRUPTED", "CANCELLED"):
            raise IntegrationStale(f"Only a FAILED/INTERRUPTED/CANCELLED run can be retried (is {old['status']})")
        b = old["input_binding"]
        return self.start_render(job_id, owner_id, language, idempotency_key, audio_version=b["audio_version"],
                                 visual_version=b["visual_version"], burn_subtitles=b["burn_subtitles"],
                                 background=background)

    def cancel_render(self, job_id: str, owner_id: str, language: str, version: int) -> dict:
        row = self.get_integration(job_id, owner_id, language, RENDER_KIND, version)
        if row["status"] not in ("QUEUED", "RUNNING"):
            raise IntegrationStale(f"Cannot cancel a render that is {row['status']}")
        self._transition(job_id, language, RENDER_KIND, version, "CANCELLED", detail="Cancelled by owner",
                         actor=owner_id)
        return self.get_integration(job_id, owner_id, language, RENDER_KIND, version)

    # -- helpers --------------------------------------------------------------

    def verify_audio_files(self, job_id: str, audio: dict) -> list[str]:
        out = []
        for s in audio["scenes"]:
            try:
                if self.store.sha256_of(job_id, s["audio_uri"]) != s["audio_sha256"]:
                    out.append(f"{s['scene_id']}: checksum mismatch")
            except Exception as exc:
                out.append(f"{s['scene_id']}: unreadable ({type(exc).__name__})")
        return out

    def _is_cancelled(self, job_id, language, version) -> bool:
        from contextlib import closing
        from defence_studio.database import connect
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT status FROM integration_artifacts WHERE job_id=? AND language=? AND kind=? AND version=?",
                          (job_id, language, RENDER_KIND, version)).fetchone()
        return bool(r and r["status"] == "CANCELLED")

    def _fail_if_running(self, job_id, language, version, detail: str) -> None:
        try:
            self._transition(job_id, language, RENDER_KIND, version, "FAILED", detail=detail,
                             allowed_from=("RUNNING",))
        except IntegrationStale:
            # Cancellation or startup recovery won the race. Keep that
            # durable terminal status instead of reviving the render as failed.
            return

    def _concat_narration(self, job_id: str, audio: dict, out_path: Path) -> tuple[float, list[dict]]:
        """Concatenate scene WAVs (+ requested pauses) into one PCM WAV. Returns
        (duration, scene spans). Parameters must match across scenes."""
        params, frames_out, spans, cursor = None, [], [], 0.0
        for s in audio["scenes"]:
            data = self.store.read_bytes(job_id, s["audio_uri"])
            with wave.open(io.BytesIO(data), "rb") as w:
                p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                n = w.getnframes()
                frames = w.readframes(n)
            if params is None:
                params = p
            elif p != params:
                raise RenderError("audio_params", f"Scene {s['scene_id']} audio format {p} differs from {params}")
            frames_out.append(frames)
            duration = n / float(p[2])
            pause = (((s.get("voice_direction") or {}).get("requested") or {}).get("pause_after_ms") or 0) / 1000.0
            if pause:
                silence = int(round(pause * p[2]))
                frames_out.append(b"\x00" * (silence * p[0] * p[1]))
            spans.append({"scene_id": s["scene_id"], "text": s["text"], "start": round(cursor, 3),
                          "duration": round(duration, 3), "span": round(duration + pause, 3)})
            cursor += duration + pause
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(params[0]); w.setsampwidth(params[1]); w.setframerate(params[2])
            for f in frames_out:
                w.writeframes(f)
        return cursor, spans

    # -- run ------------------------------------------------------------------

    def _run(self, job_id, language, version, binding, audio, timeline_body) -> None:
        step = "prepare"
        try:
            self._transition(job_id, language, RENDER_KIND, version, "RUNNING", detail="Building render package",
                             progress=0.02, allowed_from=("QUEUED",))
            self.verify_visual_asset_rights(job_id, language, binding)
            pkg_dir = self.store.job_dir(job_id) / "render" / language / f"pkg-{version:04d}"
            if pkg_dir.exists():
                shutil.rmtree(pkg_dir)
            (pkg_dir / "assets").mkdir(parents=True)
            duration, spans = self._concat_narration(job_id, audio, pkg_dir / "narration.wav")
            timeline = timeline_body["timeline"]
            tl_bytes = json.dumps(timeline, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
            (pkg_dir / "timeline.json").write_bytes(tl_bytes)
            assets: dict = {}
            ready_ids = sorted({s["asset_id"] for s in timeline["shots"] if s.get("status") == "READY" and s.get("asset_id")})
            for aid in ready_ids:
                meta = timeline["assets"][aid]
                data = self._visual().fetch_asset(aid)
                sha = hashlib.sha256(data).hexdigest()
                if meta.get("checksum_sha256") and meta["checksum_sha256"] != sha:
                    raise RenderError("asset_checksum", f"Asset {aid} checksum mismatch")
                rel = f"assets/{aid}{_EXT.get(meta.get('mime_type'), '.png')}"
                (pkg_dir / rel).write_bytes(data)
                assets[aid] = {"path": rel, "sha256": sha, "mime_type": meta.get("mime_type", "image/png")}
            out_opts = binding["output"]
            manifest = {
                "schema": "video-render-package", "version": "1",
                "timeline": "timeline.json", "timeline_sha256": hashlib.sha256(tl_bytes).hexdigest(),
                "narration": {"path": "narration.wav",
                              "sha256": hashlib.sha256((pkg_dir / "narration.wav").read_bytes()).hexdigest()},
                "assets": assets,
                "output_profile": {"width": int(out_opts.get("output_width", 1920)),
                                   "height": int(out_opts.get("output_height", 1080)),
                                   "fps": int(min(out_opts.get("frame_rate", 25), 60))},
                "source": {"system": "defence-studio", "job_id": job_id, "language": language,
                           "render_version": version},
            }
            (pkg_dir / "package.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

            step = "render"
            self._transition(job_id, language, RENDER_KIND, version, "RUNNING", detail="Rendering",
                             progress=0.1, allowed_from=("RUNNING",))

            def progress(p, done, total):
                self._transition(job_id, language, RENDER_KIND, version, "RUNNING",
                                 detail=f"Rendering frame {done}/{total}", progress=round(0.1 + 0.7 * p, 3),
                                 allowed_from=("RUNNING",))

            result = self._renderer().render(
                str(pkg_dir), f"ds-{job_id}-{language}-{version}", on_progress=progress,
                should_cancel=lambda: self._is_cancelled(job_id, language, version))
            if self._is_cancelled(job_id, language, version):
                return
            qc = result["qc"]
            if not qc.get("passed"):
                raise RenderError("renderer_qc_failed", "Renderer QC reported failure")
            plain = result["output_bytes"]
            self.verify_visual_asset_rights(job_id, language, binding)
            plain_sha = hashlib.sha256(plain).hexdigest()
            if qc.get("output", {}).get("sha256") and qc["output"]["sha256"] != plain_sha:
                raise RenderError("output_checksum", "Downloaded MP4 does not match the renderer's checksum")
            plain_uri = f"jobs/{job_id}/render/{language}/{plain_sha}.mp4"
            self.store.write_bytes(job_id, plain_uri, plain, "video/mp4")

            body = {
                "schema_version": RENDER_SCHEMA, "job_id": job_id, "language": language, "version": version,
                "binding": binding, "created_at": _now(),
                "package": {"manifest_sha256": hashlib.sha256((pkg_dir / "package.json").read_bytes()).hexdigest(),
                            "timeline_sha256": manifest["timeline_sha256"], "narration_sha256": manifest["narration"]["sha256"],
                            "asset_count": len(assets), "output_profile": manifest["output_profile"],
                            "path_note": "package folder retained under the job's artifact tree"},
                "renderer": {"job_id": result["job"]["job_id"], "qc_passed": True,
                             "renderer_version": qc.get("renderer_version"), "warnings": qc.get("warnings", []),
                             "gap_shots": (qc.get("package") or {}).get("gap_shots"),
                             "ffmpeg": (qc.get("ffmpeg") or {}).get("version"),
                             "license_flags": (qc.get("ffmpeg") or {}).get("license_flags")},
                "video_plain": {"uri": plain_uri, "sha256": plain_sha, "size_bytes": len(plain),
                                "probe": qc.get("probe"), "decode": qc.get("decode")},
                "scene_spans": spans, "audio_duration_seconds": round(duration, 3),
                "video_subtitled": None, "subtitles": None,
            }
            if binding["burn_subtitles"]:
                step = "subtitles"
                self._transition(job_id, language, RENDER_KIND, version, "RUNNING", detail="Building subtitles",
                                 progress=0.82)
                body["subtitles"], body["video_subtitled"] = self._subtitle_step(
                    job_id, language, spans, duration, plain_uri, manifest["output_profile"])
            self.verify_visual_asset_rights(job_id, language, binding)
            uri, sha = self._store_json(job_id, f"renders/{language}", body)
            self._transition(job_id, language, RENDER_KIND, version, "SUCCEEDED",
                             detail=f"{round(duration, 1)}s video, {len(spans)} scene(s)", progress=1.0,
                             uri=uri, sha256=sha, allowed_from=("RUNNING",))
        except RendererError as exc:
            self._fail_if_running(job_id, language, version,
                                  f"{step}: {exc.code}: {exc.message}")
        except (RenderError, subs.SubtitleError, VisualModuleError) as exc:
            self._fail_if_running(job_id, language, version,
                                  f"{step}: {exc.code}: {exc.message}")
        except Exception as exc:
            self._fail_if_running(job_id, language, version,
                                  f"{step}: internal: {type(exc).__name__}: {exc}")

    def _subtitle_step(self, job_id, language, spans, duration, plain_uri, profile) -> tuple[dict, dict]:
        cues = subs.build_cues([{"scene_id": s["scene_id"], "text": s["text"], "start": s["start"],
                                 "duration": s["duration"]} for s in spans])
        problems = subs.check_cues(cues, [{"scene_id": s["scene_id"], "start": s["start"], "duration": s["duration"]}
                                          for s in spans], duration)
        if problems:
            raise RenderError("subtitle_sync", "; ".join(problems[:5]))
        font_file, family = subs.resolve_font(language)
        ass = subs.to_ass(cues, profile["width"], profile["height"], family)
        srt, vtt = subs.to_srt(cues), subs.to_vtt(cues)
        files = {}
        for name, text, mime in (("srt", srt, "application/x-subrip"), ("vtt", vtt, "text/vtt"),
                                 ("ass", ass, "text/x-ssa")):
            data = text.encode("utf-8")
            sha = hashlib.sha256(data).hexdigest()
            uri = f"jobs/{job_id}/render/{language}/{sha}.{name}"
            self.store.write_bytes(job_id, uri, data, mime)
            files[name] = {"uri": uri, "sha256": sha}
        src = self.store.job_dir(job_id) / plain_uri.split(f"jobs/{job_id}/", 1)[1]
        out_tmp = Path(tempfile.mkdtemp(prefix="ds-sub-")) / "subtitled.mp4"
        try:
            info = subs.burn_in(src, ass, out_tmp, font_file)
            probe = subs.probe(out_tmp)
            errors = subs.full_decode_errors(out_tmp)
            if errors:
                raise RenderError("decode_errors", f"Subtitled video has {errors} decode error line(s)")
            if abs(probe["duration"] - duration) > 0.25:
                raise RenderError("duration_drift", f"Subtitled video {probe['duration']:.2f}s vs audio {duration:.2f}s")
            data = out_tmp.read_bytes()
        finally:
            shutil.rmtree(out_tmp.parent, ignore_errors=True)
        sha = hashlib.sha256(data).hexdigest()
        uri = f"jobs/{job_id}/render/{language}/{sha}-subtitled.mp4"
        self.store.write_bytes(job_id, uri, data, "video/mp4")
        sub_meta = {"cue_count": len(cues), "cue_timing": subs.CUE_TIMING_METHOD, "font_family": family,
                    "font_file": info["font_file"], "files": files, "sync_checks": "passed",
                    "scene_boundaries": "measured from narration audio"}
        return sub_meta, {"uri": uri, "sha256": sha, "size_bytes": len(data), "probe": probe, "decode_errors": 0}

    # -- reads ----------------------------------------------------------------

    def render_status(self, job_id: str, owner_id: str, language: str) -> dict:
        self._check_ownership(job_id, owner_id)
        try:
            current, _ = self._approved_plan(job_id, owner_id, language)
            cur = current["plan_sha256"]
        except Exception:
            cur = None
        items = self._integration_rows(job_id, language, RENDER_KIND)
        for it in items:
            it["stale"] = cur is None or it["input_binding"].get("scene_plan_sha256") != cur
        return {"job_id": job_id, "language": language, "current_scene_plan_sha256": cur, "items": items}

    def render_file(self, job_id: str, owner_id: str, language: str, version: int, which: str) -> tuple[bytes, str]:
        body = self.read_integration_body(job_id, owner_id, language, RENDER_KIND, version)
        visual = self.get_integration(job_id, owner_id, language, "visual_timeline", body["binding"]["visual_version"])
        self.verify_visual_asset_rights(job_id, language, visual["input_binding"])
        if which in ("plain", "subtitled"):
            ref = body["video_plain"] if which == "plain" else body.get("video_subtitled")
            mime = "video/mp4"
        elif which in ("srt", "vtt", "ass") and body.get("subtitles"):
            ref, mime = body["subtitles"]["files"][which], "text/plain; charset=utf-8"
        else:
            ref = None
        if not ref:
            raise IntegrationStale(f"This render has no {which!r} output")
        data = self.store.read_bytes(job_id, ref["uri"])
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise IntegrationStale("Stored output failed its integrity check")
        return data, mime
