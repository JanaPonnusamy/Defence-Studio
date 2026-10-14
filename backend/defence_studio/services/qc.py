"""RM-6: automated quality control and exact-version final approval.

`run_qc` produces a versioned ``defence-studio.qc-report.v1`` (+ a readable
Markdown rendering) for one SUCCEEDED render. Every measurement comes from
ffprobe/ffmpeg on the stored file, the stored artifacts' hashes, or the font
file -- never from the renderer's own claim of success. FFmpeg exiting 0 is
one check among many, not an approval.

Final approval (`approve_render`) is a separate, explicit, owner action bound
to the exact render version + video SHA-256 + QC-report SHA-256. It is refused
while any BLOCKER/ERROR check failed, requires every WARN to be acknowledged by
id, and is invalidated (detectably) by any newer QC report or render.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from defence_studio.integrations import media_qc, subtitles as subs
from defence_studio.services.integration import (
    IntegrationService, IntegrationStale, IntegrationValidationError,
)
from defence_studio.services.render import RENDER_KIND
from defence_studio.services.script import _now

QC_KIND = "qc_report"
QC_SCHEMA = "defence-studio.qc-report.v1"
APPROVAL_KIND = "final_approval"
APPROVAL_SCHEMA = "defence-studio.final-approval.v1"
QC_VERSION = "defence-studio.qc.v1"

DEFAULT_REQUIREMENTS = {
    "min_seconds": 5.0, "max_seconds": 3600.0, "duration_tolerance_seconds": 0.25,
    "loudness_lufs_range": [-24.0, -12.0], "true_peak_max_dbfs": -1.0, "clip_peak_db": -0.1,
    "max_cue_chars": 110, "require_branding": False,
}
# check id -> stage a failure points at (for selective rerun)
RERUN = {"audio": "audio", "visual": "visual_timeline", "render": "render", "assets": "assets",
         "scene": "scene_plan", "script": "script"}


def _check(cid, status, severity, detail, stage="render", **evidence):
    return {"id": cid, "status": status, "severity": severity, "detail": detail,
            "rerun_stage": RERUN.get(stage, stage), "evidence": evidence}


class QcService(IntegrationService):
    # -- run ------------------------------------------------------------------

    def start_qc(self, job_id: str, owner_id: str, language: str, render_version: int, idempotency_key: str, *,
                 requirements: dict | None = None, background: bool = True) -> dict:
        render = self.get_integration(job_id, owner_id, language, RENDER_KIND, render_version)
        if render["status"] != "SUCCEEDED":
            raise IntegrationStale(f"Render v{render_version} is {render['status']}; QC needs a SUCCEEDED render")
        req = {**DEFAULT_REQUIREMENTS, **(requirements or {})}
        unknown = set(req) - set(DEFAULT_REQUIREMENTS)
        if unknown:
            raise IntegrationValidationError(f"Unknown QC requirement(s): {sorted(unknown)}")
        binding = {"render_version": render_version, "render_sha256": render["sha256"],
                   "scene_plan_sha256": render["input_binding"]["scene_plan_sha256"], "requirements": req,
                   "qc_version": QC_VERSION}
        item, created = self._create_row(job_id, owner_id, language, QC_KIND, binding, idempotency_key)
        if created:
            self._launch(lambda: self._run_qc(job_id, owner_id, language, item["version"], binding), background)
        return self.get_integration(job_id, owner_id, language, QC_KIND, item["version"])

    def _run_qc(self, job_id, owner_id, language, version, binding) -> None:
        try:
            self._transition(job_id, language, QC_KIND, version, "RUNNING", detail="Analysing media", progress=0.05,
                             allowed_from=("QUEUED",))
            report = self.evaluate(job_id, owner_id, language, binding)
            report["version"] = version
            uri, sha = self._store_json(job_id, f"qc/{language}", report)
            s = report["summary"]
            self._transition(job_id, language, QC_KIND, version, "SUCCEEDED",
                             detail=f"{'PASS' if s['passed'] else 'FAIL'}: {s['blockers_and_errors']} blocking, "
                                    f"{s['warnings']} warning(s)", progress=1.0, uri=uri, sha256=sha)
        except Exception as exc:
            self._transition(job_id, language, QC_KIND, version, "FAILED",
                             detail=f"internal: {type(exc).__name__}: {exc}")

    # -- evaluation (pure function of stored artifacts + the media file) -------

    def evaluate(self, job_id: str, owner_id: str, language: str, binding: dict) -> dict:
        req = binding["requirements"]
        checks: list[dict] = []
        measurements: dict = {}
        rv = binding["render_version"]
        render_row = self.get_integration(job_id, owner_id, language, RENDER_KIND, rv)
        render = self.read_integration_body(job_id, owner_id, language, RENDER_KIND, rv)
        rb = render["binding"]

        # -- manifest / binding consistency
        problems = []
        if render_row["sha256"] != binding["render_sha256"]:
            problems.append("render artifact hash differs from the QC binding")
        try:
            plan_row, plan = self._approved_plan(job_id, owner_id, language)
            if plan_row["plan_sha256"] != rb["scene_plan_sha256"]:
                problems.append("scene plan changed after this render")
        except Exception as exc:
            plan, plan_row = None, None
            problems.append(f"no current approved scene plan ({type(exc).__name__})")
        audio_row = self.get_integration(job_id, owner_id, language, "audio_set", rb["audio_version"])
        visual_row = self.get_integration(job_id, owner_id, language, "visual_timeline", rb["visual_version"])
        if audio_row["sha256"] != rb["audio_set_sha256"]:
            problems.append("audio set hash differs from the render binding")
        if visual_row["sha256"] != rb["visual_sha256"]:
            problems.append("visual timeline hash differs from the render binding")
        checks.append(_check("MANIFEST_CONSISTENCY", "FAIL" if problems else "PASS", "BLOCKER",
                             "; ".join(problems) or "Render, audio, visual and scene-plan hashes are consistent",
                             "render"))

        # -- file integrity
        video_ref = render.get("video_subtitled") or render["video_plain"]
        measurements["video_checked"] = "subtitled" if render.get("video_subtitled") else "plain"
        try:
            data = self.store.read_bytes(job_id, video_ref["uri"])
            ok = hashlib.sha256(data).hexdigest() == video_ref["sha256"] and len(data) > 0
            checks.append(_check("OUTPUT_INTEGRITY", "PASS" if ok else "FAIL", "BLOCKER",
                                 "Stored video re-hashes to its recorded SHA-256" if ok else "Stored video failed its hash check",
                                 "render", sha256=video_ref["sha256"], size_bytes=len(data)))
        except Exception as exc:
            checks.append(_check("OUTPUT_INTEGRITY", "FAIL", "BLOCKER", f"Stored video unreadable: {type(exc).__name__}",
                                 "render"))
            return self._finish(job_id, language, binding, checks, measurements, render)
        video_path = self.store.job_dir(job_id) / video_ref["uri"].split(f"jobs/{job_id}/", 1)[1]

        audio = self.read_integration_body(job_id, owner_id, language, "audio_set", rb["audio_version"])
        bad_audio = self._verify_audio(job_id, audio)
        mock = audio["summary"]["any_mock"]
        checks.append(_check("AUDIO_INTEGRITY", "FAIL" if (bad_audio or mock) else "PASS", "BLOCKER",
                             ("; ".join(bad_audio) if bad_audio else "") + (" Mock TTS audio is not narration." if mock else "")
                             or "All scene audio files re-hash; no mock audio", "audio"))

        # -- container / streams / duration / decode
        try:
            probe = subs.probe(video_path)
        except Exception as exc:
            checks.append(_check("CONTAINER_STREAMS", "FAIL", "BLOCKER", f"ffprobe failed: {exc}", "render"))
            return self._finish(job_id, language, binding, checks, measurements, render)
        measurements["probe"] = probe
        out = rb.get("output") or {}
        want_w, want_h = int(out.get("output_width", 1920)), int(out.get("output_height", 1080))
        v, a = probe["video"], probe["audio"]
        issues = []
        if "mp4" not in (probe["container"] or ""):
            issues.append(f"container {probe['container']}")
        if not v or v["codec"] != "h264":
            issues.append("video is not H.264")
        if not a or a["codec"] != "aac":
            issues.append("audio stream is missing or not AAC")
        if v and (v["width"], v["height"]) != (want_w, want_h):
            issues.append(f"resolution {v['width']}x{v['height']} != {want_w}x{want_h}")
        checks.append(_check("CONTAINER_STREAMS", "FAIL" if issues else "PASS", "ERROR",
                             "; ".join(issues) or f"mp4 / h264 {want_w}x{want_h} / aac", "render"))
        dur, expect = probe["duration"], float(render["audio_duration_seconds"])
        tol = float(req["duration_tolerance_seconds"])
        checks.append(_check("DURATION_MATCH", "PASS" if abs(dur - expect) <= tol else "FAIL", "ERROR",
                             f"video {dur:.2f}s vs narration {expect:.2f}s (tolerance {tol}s)", "render"))
        in_range = req["min_seconds"] <= dur <= req["max_seconds"]
        checks.append(_check("DURATION_LIMITS", "PASS" if in_range else "FAIL", "ERROR",
                             f"{dur:.1f}s within [{req['min_seconds']}, {req['max_seconds']}]" if in_range else
                             f"{dur:.1f}s outside [{req['min_seconds']}, {req['max_seconds']}]", "script"))
        errors = subs.full_decode_errors(video_path)
        checks.append(_check("FULL_DECODE", "PASS" if errors == 0 else "FAIL", "BLOCKER",
                             "Full decode produced no errors" if errors == 0 else f"{errors} decode error line(s)",
                             "render"))

        # -- visual coverage
        timeline = self.read_integration_body(job_id, owner_id, language, "visual_timeline",
                                              rb["visual_version"])["timeline"]
        shots = timeline.get("shots", [])
        gaps = [s for s in shots if s.get("status") == "GAP"]
        covered = {sid for s in shots for sid in s.get("narration_anchor", {}).get("segment_ids", [])}
        scene_ids = [s["scene_id"] for s in (plan["scenes"] if plan else [])]
        missing = [sid for sid in scene_ids if sid not in covered]
        checks.append(_check("VISUAL_COVERAGE", "FAIL" if (gaps or missing) else "PASS", "BLOCKER",
                             f"{len(gaps)} gap shot(s) have no rights-cleared visual; {len(missing)} scene(s) uncovered"
                             if (gaps or missing) else "Every scene has a real, rights-cleared visual", "assets",
                             gap_shots=[g["shot_id"] for g in gaps], missing_scenes=missing))

        # -- ffmpeg analysis
        try:
            m = media_qc.analyze_media(video_path)
        except Exception as exc:
            checks.append(_check("MEDIA_ANALYSIS", "FAIL", "ERROR", f"ffmpeg analysis failed: {exc}", "render"))
            return self._finish(job_id, language, binding, checks, measurements, render)
        measurements["analysis"] = m
        blacks = [b for b in m["black_intervals"]
                  if not (b["start"] < 0.1 or b["end"] > dur - 0.1) or (b["end"] - b["start"]) > 1.5]
        checks.append(_check("BLACK_FRAMES", "FAIL" if blacks else "PASS", "ERROR",
                             f"{len(blacks)} black interval(s): " + ", ".join(f"{b['start']:.1f}-{b['end']:.1f}s" for b in blacks[:5])
                             if blacks else "No unintended black sections", "visual", intervals=blacks))
        moving = {s["shot_id"]: s for s in shots if (s.get("motion") or {}).get("type", "STATIC") != "STATIC"
                  and s.get("status") == "READY"}
        frozen = []
        for fz in m["frozen_intervals"]:
            for s in moving.values():
                if fz["start"] < s["end"] and fz["end"] > s["start"]:
                    frozen.append({**fz, "shot_id": s["shot_id"]})
        checks.append(_check("FROZEN_FRAMES", "WARN" if frozen else "PASS", "WARNING",
                             f"{len(frozen)} frozen interval(s) inside shots with planned motion" if frozen
                             else "No unintended frozen sections", "visual", intervals=frozen))
        peak = m["max_volume_db"]
        clip = peak is not None and peak >= req["clip_peak_db"]
        checks.append(_check("AUDIO_CLIPPING", "FAIL" if clip else "PASS", "ERROR",
                             f"Peak {peak} dB" + (" reaches full scale (clipping)" if clip else ""), "audio"))
        long_sil = [s for s in m["silences"] if s["end"] - s["start"] >= 3.0]
        checks.append(_check("AUDIO_SILENCE", "WARN" if long_sil else "PASS", "WARNING",
                             f"{len(long_sil)} silence(s) of 3s or more" if long_sil else "No long silences",
                             "audio", intervals=long_sil))
        lufs = m["integrated_lufs"]
        lo, hi = req["loudness_lufs_range"]
        if lufs is None:
            checks.append(_check("LOUDNESS", "WARN", "WARNING", "Integrated loudness could not be measured", "audio"))
        else:
            checks.append(_check("LOUDNESS", "PASS" if lo <= lufs <= hi else "WARN", "WARNING",
                                 f"Integrated {lufs:.1f} LUFS (target {lo}..{hi})", "audio",
                                 integrated_lufs=lufs, true_peak_dbfs=m["true_peak_dbfs"]))

        # -- subtitles
        subtitles = render.get("subtitles")
        if rb.get("burn_subtitles"):
            if not subtitles or not render.get("video_subtitled"):
                checks.append(_check("SUBTITLES_PRESENT", "FAIL", "ERROR", "Subtitles were requested but not produced",
                                     "render"))
            else:
                srt = self.store.read_bytes(job_id, subtitles["files"]["srt"]["uri"]).decode("utf-8")
                checks.append(_check("SUBTITLES_PRESENT", "PASS", "ERROR",
                                     f"{subtitles['cue_count']} cue(s) burned in; SRT/VTT/ASS exported", "render"))
                cues = self._parse_srt(srt)
                spans = render["scene_spans"]
                for c in cues:  # SRT carries no scene ids: attribute by time
                    mid = (c["start"] + c["end"]) / 2
                    owner = next((sp for sp in spans if sp["start"] - 0.05 <= mid <= sp["start"] + sp["duration"] + 0.05), None)
                    c["scene_id"] = owner["scene_id"] if owner else "?"
                probs = subs.check_cues(cues, [{"scene_id": s["scene_id"], "start": s["start"],
                                                "duration": s["duration"]} for s in spans], expect)
                checks.append(_check("SUBTITLE_SYNC", "FAIL" if probs else "PASS", "ERROR",
                                     "; ".join(probs[:4]) if probs else "Every cue lies inside its scene's measured audio span",
                                     "render", timing=subtitles["cue_timing"]))
                long_cues = [c["index"] for c in cues if len(c["text"]) > req["max_cue_chars"]]
                checks.append(_check("SUBTITLE_OVERFLOW", "WARN" if long_cues else "PASS", "WARNING",
                                     f"cue(s) over {req['max_cue_chars']} characters: {long_cues[:8]}" if long_cues
                                     else "No over-long cues", "render"))
                try:
                    font_file, family = subs.resolve_font(language)
                    text = "".join(c["text"] for c in cues)
                    miss = media_qc.missing_glyphs(text, font_file)
                    checks.append(_check("SUBTITLE_GLYPHS", "FAIL" if miss else "PASS", "ERROR",
                                         f"Font {family} lacks {len(miss)} character(s): {miss[:10]}" if miss
                                         else f"Font {family} covers every subtitle character ({language})",
                                         "render", font=family))
                except Exception as exc:
                    checks.append(_check("SUBTITLE_GLYPHS", "WARN", "WARNING", f"Glyph coverage not verified: {exc}",
                                         "render"))
        else:
            checks.append(_check("SUBTITLES_PRESENT", "NOT_CHECKED", "INFO", "Subtitles were not requested for this render",
                                 "render"))

        # -- provenance and evidence
        assets = timeline.get("assets", {})
        bad = [aid for aid, a_ in assets.items() if a_.get("rights_status") in ("RESTRICTED", "UNKNOWN")
               or a_.get("provenance_class") in ("PLACEHOLDER", "TEST_FIXTURE")]
        attr = [aid for aid, a_ in assets.items() if a_.get("attribution_required")]
        ai = [aid for aid, a_ in assets.items() if a_.get("provenance_class") == "AI_GENERATED"
              or a_.get("editorial_label") == "AI_GENERATED"]
        checks.append(_check("ASSET_RIGHTS", "FAIL" if bad else "PASS", "BLOCKER",
                             f"{len(bad)} asset(s) with unknown/restricted rights or placeholder provenance" if bad
                             else f"{len(assets)} asset(s): rights usable", "assets", assets=bad))
        unlabeled = [s["shot_id"] for s in shots if s.get("asset_id") in ai
                     and not any(o.get("type") == "editorial_label" for o in s.get("overlays", []))]
        checks.append(_check("AI_LABELLING", "FAIL" if unlabeled else "PASS", "ERROR",
                             f"AI-generated visuals without an on-screen label: {unlabeled}" if unlabeled
                             else f"{len(ai)} AI-generated asset(s), all labelled", "visual"))
        checks.append(_check("ATTRIBUTION_REQUIRED", "WARN" if attr else "PASS", "WARNING",
                             f"{len(attr)} asset(s) require crediting in the description/credits" if attr
                             else "No attribution-required assets", "assets", assets=attr))
        if plan:
            no_claim = [s["scene_id"] for s in plan["scenes"]
                        if not s.get("claim_ids") and not str(s.get("source_section_id", "")).startswith("__")]
            checks.append(_check("EVIDENCE_REFS", "WARN" if no_claim else "PASS", "WARNING",
                                 f"{len(no_claim)} factual scene(s) cite no approved claim" if no_claim
                                 else "Every factual scene cites approved evidence", "scene", scenes=no_claim))
        checks.append(_check("BRANDING_ASSETS", "NOT_CHECKED" if not req["require_branding"] else "FAIL",
                             "INFO" if not req["require_branding"] else "ERROR",
                             "Intro/outro/metadata assets are produced by the branding stage (RM-7); not required here"
                             if not req["require_branding"] else "Branding was required but no branding stage output exists",
                             "branding"))
        return self._finish(job_id, language, binding, checks, measurements, render)

    def _finish(self, job_id, language, binding, checks, measurements, render) -> dict:
        blocking = [c for c in checks if c["status"] == "FAIL" and c["severity"] in ("BLOCKER", "ERROR")]
        warns = [c for c in checks if c["status"] == "WARN"]
        stages = sorted({c["rerun_stage"] for c in blocking})
        return {
            "schema_version": QC_SCHEMA, "qc_version": QC_VERSION, "job_id": job_id, "language": language,
            "generated_at": _now(), "binding": binding,
            "render": {"video_sha256": (render.get("video_subtitled") or render["video_plain"])["sha256"],
                       "renderer_qc_passed": render["renderer"]["qc_passed"]},
            "checks": checks, "measurements": measurements,
            "summary": {"passed": not blocking, "blockers_and_errors": len(blocking), "warnings": len(warns),
                        "not_checked": len([c for c in checks if c["status"] == "NOT_CHECKED"]),
                        "checks_run": len(checks), "rerun_stages": stages},
        }

    # -- helpers --------------------------------------------------------------

    def _verify_audio(self, job_id, audio) -> list[str]:
        out = []
        for s in audio["scenes"]:
            try:
                if self.store.sha256_of(job_id, s["audio_uri"]) != s["audio_sha256"]:
                    out.append(f"{s['scene_id']}: checksum mismatch")
            except Exception:
                out.append(f"{s['scene_id']}: unreadable")
        return out

    @staticmethod
    def _parse_srt(text: str) -> list[dict]:
        import re
        cues = []
        for block in re.split(r"\n\s*\n", text.strip()):
            lines = block.strip().splitlines()
            if len(lines) < 3:
                continue
            m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
            if not m:
                continue
            g = [int(x) for x in m.groups()]
            cues.append({"index": int(lines[0]), "start": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                         "end": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, "text": " ".join(lines[2:])})
        # scene ids are not in SRT; recover them from the render's scene spans
        return cues

    @staticmethod
    def _markdown(report: dict) -> str:
        s = report["summary"]
        lines = [f"# QC report — {'PASS' if s['passed'] else 'FAIL'}", "",
                 f"Job `{report['job_id']}` · language `{report['language']}` · generated {report['generated_at']}",
                 f"Video SHA-256 `{report['render']['video_sha256']}`", "",
                 f"{s['blockers_and_errors']} blocking, {s['warnings']} warning(s), {s['not_checked']} not checked.", "",
                 "| Check | Status | Severity | Detail |", "|---|---|---|---|"]
        for c in report["checks"]:
            detail = c["detail"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {c['id']} | {c['status']} | {c['severity']} | {detail} |")
        if s["rerun_stages"]:
            lines += ["", "Stages to re-run: " + ", ".join(s["rerun_stages"])]
        return "\n".join(lines) + "\n"

    # -- reads ------------------------------------------------------------------

    def qc_status(self, job_id: str, owner_id: str, language: str) -> dict:
        self._check_ownership(job_id, owner_id)
        rows = self._integration_rows(job_id, language, QC_KIND)
        approvals = self._integration_rows(job_id, language, APPROVAL_KIND)
        return {"job_id": job_id, "language": language, "reports": rows, "approvals": approvals}

    def read_report(self, job_id, owner_id, language, version) -> dict:
        return self.read_integration_body(job_id, owner_id, language, QC_KIND, version)

    def read_report_markdown(self, job_id, owner_id, language, version) -> str:
        return self._markdown(self.read_report(job_id, owner_id, language, version))

    # -- final approval ---------------------------------------------------------

    def approve_render(self, job_id: str, owner_id: str, language: str, qc_version: int, note: str,
                       acknowledged_warnings: list[str], idempotency_key: str) -> dict:
        return self._decide(job_id, owner_id, language, qc_version, note, acknowledged_warnings,
                            idempotency_key, "APPROVED")

    def reject_render(self, job_id: str, owner_id: str, language: str, qc_version: int, note: str,
                      idempotency_key: str) -> dict:
        return self._decide(job_id, owner_id, language, qc_version, note, [], idempotency_key, "REJECTED")

    def _decide(self, job_id, owner_id, language, qc_version, note, acked, idempotency_key, decision) -> dict:
        note = (note or "").strip()
        if not note:
            raise IntegrationValidationError("A note is required")
        row = self.get_integration(job_id, owner_id, language, QC_KIND, qc_version)
        if row["status"] != "SUCCEEDED":
            raise IntegrationStale(f"QC report v{qc_version} is {row['status']}")
        report = self.read_integration_body(job_id, owner_id, language, QC_KIND, qc_version)
        latest = max(r["version"] for r in self._integration_rows(job_id, language, QC_KIND)
                     if r["status"] == "SUCCEEDED")
        if qc_version != latest:
            raise IntegrationStale("A newer QC report exists; review that one")
        b = report["binding"]
        render_row = self.get_integration(job_id, owner_id, language, RENDER_KIND, b["render_version"])
        newest_render = max(r["version"] for r in self._integration_rows(job_id, language, RENDER_KIND)
                            if r["status"] == "SUCCEEDED")
        if render_row["sha256"] != b["render_sha256"] or b["render_version"] != newest_render:
            raise IntegrationStale("The render changed after this QC report; re-run QC")
        if decision == "APPROVED":
            if not report["summary"]["passed"]:
                raise IntegrationValidationError(
                    "QC has blocking failures (" + ", ".join(c["id"] for c in report["checks"]
                                                             if c["status"] == "FAIL" and c["severity"] in ("BLOCKER", "ERROR"))
                    + "); a render cannot be approved until they are fixed")
            warn_ids = sorted(c["id"] for c in report["checks"] if c["status"] == "WARN")
            missing = [w for w in warn_ids if w not in set(acked)]
            if missing:
                raise IntegrationValidationError("Acknowledge every warning by id before approving: " + ", ".join(missing))
        binding = {"qc_version": qc_version, "qc_sha256": row["sha256"], "render_version": b["render_version"],
                   "render_sha256": b["render_sha256"], "video_sha256": report["render"]["video_sha256"],
                   "decision": decision}
        item, created = self._create_row(job_id, owner_id, language, APPROVAL_KIND, binding, idempotency_key,
                                         reuse_active=True)
        if created:
            record = {"schema_version": APPROVAL_SCHEMA, "job_id": job_id, "language": language, "binding": binding,
                      "reviewer_id": owner_id, "note": note, "acknowledged_warnings": sorted(acked),
                      "decided_at": _now()}
            uri, sha = self._store_json(job_id, f"approvals/{language}", record)
            self._transition(job_id, language, APPROVAL_KIND, item["version"], decision, detail=note[:500],
                             uri=uri, sha256=sha, actor=owner_id, progress=1.0)
        return self.get_integration(job_id, owner_id, language, APPROVAL_KIND, item["version"])

    def approved_render(self, job_id: str, owner_id: str, language: str) -> dict:
        """The export gate: the newest APPROVED decision, valid only while its
        QC report and render are still the newest ones."""
        self._check_ownership(job_id, owner_id)
        rows = [r for r in self._integration_rows(job_id, language, APPROVAL_KIND)]
        if not rows:
            raise IntegrationStale("No final approval exists")
        last = rows[-1]
        if last["status"] != "APPROVED":
            raise IntegrationStale(f"The latest final decision is {last['status']}")
        b = last["input_binding"]
        qcs = [r for r in self._integration_rows(job_id, language, QC_KIND) if r["status"] == "SUCCEEDED"]
        renders = [r for r in self._integration_rows(job_id, language, RENDER_KIND) if r["status"] == "SUCCEEDED"]
        if not qcs or qcs[-1]["version"] != b["qc_version"] or qcs[-1]["sha256"] != b["qc_sha256"]:
            raise IntegrationStale("A newer QC report exists; the approval no longer applies")
        if not renders or renders[-1]["version"] != b["render_version"] or renders[-1]["sha256"] != b["render_sha256"]:
            raise IntegrationStale("A newer render exists; the approval no longer applies")
        return {"approval": last, "qc_version": b["qc_version"], "render_version": b["render_version"],
                "video_sha256": b["video_sha256"]}
