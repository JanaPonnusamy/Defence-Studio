"""RM-3: approved scene plan -> renderer-neutral visual timeline artifact.

Calls the standalone Visual Module through its public API only
(`integrations.visual_module`). The persisted artifact
(`defence-studio.visual-timeline.v1`) wraps the module's timeline verbatim and
binds it to the exact approved scene-plan hash and script revision, so a
changed script/plan can never silently reuse a stale timeline.

Honesty: scene timing sent to the module is the plan's *estimate*; the artifact
records ``timing_origin: scene_plan_estimate`` and is re-derived from measured
alignment once audio exists (RM-4). GAP shots (no rights-cleared asset yet) are
surfaced, never filled.
"""
from __future__ import annotations

from contextlib import closing

from defence_studio.database import connect
from defence_studio.integrations.visual_module import VisualModuleClient, VisualModuleError
from defence_studio.services.integration import (
    IntegrationError, IntegrationService, IntegrationStale, IntegrationValidationError,
)
from defence_studio.services.script import _now

VISUAL_KIND = "visual_timeline"
VISUAL_SCHEMA = "defence-studio.visual-timeline.v1"
DEFAULT_OPTIONS = {"generate_ai": False, "allow_unknown_rights": False, "output_width": 1920,
                   "output_height": 1080, "frame_rate": 30}


class VisualService(IntegrationService):
    def __init__(self, database_file, storage_root, *, provider=None, visual_client=None):
        super().__init__(database_file, storage_root, provider=provider)
        self._visual_client = visual_client

    def _client(self) -> VisualModuleClient:
        return self._visual_client or VisualModuleClient()

    # -- inputs ---------------------------------------------------------------

    @staticmethod
    def _scene_segments(plan: dict, audio: dict | None = None) -> list[dict]:
        """Estimated timing from the plan, or *measured* timing from a verified
        audio set (scene audio duration + requested pause)."""
        measured = {s["scene_id"]: s for s in (audio or {}).get("scenes", [])}
        t, out = 0.0, []
        for scene in plan["scenes"]:
            if audio is not None:
                entry = measured.get(scene["scene_id"])
                if entry is None:
                    raise IntegrationStale(f"Audio set has no scene {scene['scene_id']!r}")
                pause = ((entry.get("voice_direction") or {}).get("requested") or {}).get("pause_after_ms", 0) or 0
                seconds = float(entry["duration_seconds"]) + pause / 1000.0
            else:
                seconds = max(float(scene["planned_timing"]["estimated_seconds"]), 0.5)
            out.append({"scene_id": scene["scene_id"], "text": scene["narration"], "start": t, "end": t + seconds})
            t += seconds
        return out

    def _measured_audio(self, job_id: str, owner_id: str, language: str, audio_version: int, plan_sha: str) -> dict:
        row = self.get_integration(job_id, owner_id, language, "audio_set", audio_version)
        if row["status"] != "SUCCEEDED":
            raise IntegrationStale(f"Audio v{audio_version} is {row['status']}, not SUCCEEDED")
        if row["input_binding"].get("scene_plan_sha256") != plan_sha:
            raise IntegrationStale("That audio was generated for a different scene plan")
        body = self.read_integration_body(job_id, owner_id, language, "audio_set", audio_version)
        if body["summary"]["any_mock"]:
            raise IntegrationValidationError("Mock audio is never accepted as narration")
        return body

    # -- operations -----------------------------------------------------------

    def start_visual_plan(self, job_id: str, owner_id: str, language: str, idempotency_key: str,
                          options: dict | None = None, *, background: bool = True,
                          audio_version: int | None = None) -> dict:
        row, plan = self._approved_plan(job_id, owner_id, language)
        opts = {**DEFAULT_OPTIONS, **(options or {})}
        if opts.get("generate_ai"):
            raise IntegrationValidationError(
                "AI image generation is not enabled from this endpoint; it is a separately budgeted, "
                "approved action (cost must be authorized first).")
        audio = None
        binding = {"scene_plan_sha256": row["plan_sha256"], "scene_plan_version": row["version"],
                   "script_revision_sha256": row["script_revision_sha256"], "package_sha256": row["package_sha256"],
                   "options": opts, "timing": "scene_plan_estimate"}
        if audio_version is not None:
            audio = self._measured_audio(job_id, owner_id, language, audio_version, row["plan_sha256"])
            arow = self.get_integration(job_id, owner_id, language, "audio_set", audio_version)
            binding.update(timing="measured_audio", audio_version=audio_version, audio_set_sha256=arow["sha256"])
        item, created = self._create_row(job_id, owner_id, language, VISUAL_KIND, binding, idempotency_key)
        if created:
            self._launch(lambda: self._run(job_id, owner_id, language, item["version"], plan, binding, opts, audio),
                         background)
        return self.get_integration(job_id, owner_id, language, VISUAL_KIND, item["version"])

    def retry_visual_plan(self, job_id: str, owner_id: str, language: str, version: int,
                          idempotency_key: str, *, background: bool = True) -> dict:
        old = self.get_integration(job_id, owner_id, language, VISUAL_KIND, version)
        if old["status"] not in ("FAILED", "INTERRUPTED", "CANCELLED"):
            raise IntegrationStale(f"Only a FAILED/INTERRUPTED/CANCELLED run can be retried (is {old['status']})")
        row, plan = self._approved_plan(job_id, owner_id, language)
        binding = dict(old["input_binding"])
        if binding["scene_plan_sha256"] != row["plan_sha256"]:
            raise IntegrationStale("The scene plan changed since that run; start a new visual plan instead")
        audio = None
        if binding.get("audio_version") is not None:
            audio = self._measured_audio(job_id, owner_id, language, binding["audio_version"], row["plan_sha256"])
        item, created = self._create_row(job_id, owner_id, language, VISUAL_KIND, binding, idempotency_key,
                                         reuse_active=True)
        if created:
            self._launch(lambda: self._run(job_id, owner_id, language, item["version"], plan, binding,
                                           binding["options"], audio), background)
        return self.get_integration(job_id, owner_id, language, VISUAL_KIND, item["version"])

    def _run(self, job_id, owner_id, language, version, plan, binding, options, audio=None) -> None:
        try:
            self._transition(job_id, language, VISUAL_KIND, version, "RUNNING", detail="Calling Visual Module",
                             progress=0.05, allowed_from=("QUEUED",))
            segments = self._scene_segments(plan, audio)

            def progress(p: float, stage: str) -> None:
                self._transition(job_id, language, VISUAL_KIND, version, "RUNNING", detail=stage,
                                 progress=round(0.1 + 0.8 * p, 3))

            result = self._client().build_timeline(
                f"{job_id}:{language}:v{version}", segments, options,
                idempotency_key=f"ds-{job_id}-{language}-{version}", on_progress=progress)
            timeline = result["timeline"]
            shots = timeline.get("shots", [])
            gaps = [s for s in shots if s.get("status") == "GAP"]
            body = {
                "schema_version": VISUAL_SCHEMA, "job_id": job_id, "language": language, "version": version,
                "binding": binding, "created_at": _now(),
                "source_module": {"name": "video-visual-module", "version": result.get("module_version"),
                                  "timeline_schema": timeline.get("schema"), "timeline_version": timeline.get("version"),
                                  "project_id": result["project_id"], "visual_plan_id": result["visual_plan_id"]},
                "timing_origin": binding.get("timing", "scene_plan_estimate"),
                "scene_map": [{"scene_id": s["scene_id"], "start": s["start"], "end": s["end"]} for s in segments],
                "summary": {"shot_count": len(shots), "gap_count": len(gaps),
                            "duration_seconds": timeline.get("duration"),
                            "gaps_by_code": _count(g.get("gap_code") for g in gaps),
                            "ready_for_render": bool(shots) and not gaps},
                "timeline": timeline,
            }
            missing = {s["scene_id"] for s in segments} - {
                sid for sh in shots for sid in sh.get("narration_anchor", {}).get("segment_ids", [])}
            if missing:
                raise VisualModuleError("scene_coverage", f"Timeline does not cover scene(s): {sorted(missing)}")
            uri, sha = self._store_json(job_id, f"visual/{language}", body)
            self._transition(job_id, language, VISUAL_KIND, version, "SUCCEEDED",
                             detail=f"{len(shots)} shot(s), {len(gaps)} gap(s)", progress=1.0, uri=uri, sha256=sha)
        except VisualModuleError as exc:
            self._transition(job_id, language, VISUAL_KIND, version, "FAILED", detail=f"{exc.code}: {exc.message}")
        except Exception as exc:  # never leave a row RUNNING
            self._transition(job_id, language, VISUAL_KIND, version, "FAILED",
                             detail=f"internal: {type(exc).__name__}: {exc}")

    # -- reads ----------------------------------------------------------------

    def visual_status(self, job_id: str, owner_id: str, language: str) -> dict:
        """All visual-timeline runs with a derived `stale` flag (never written)."""
        self._check_ownership(job_id, owner_id)
        try:
            current, _ = self._approved_plan(job_id, owner_id, language)
            current_sha = current["plan_sha256"]
        except (IntegrationStale, IntegrationError):
            current_sha = None
        items = self._integration_rows(job_id, language, VISUAL_KIND)
        for it in items:
            it["stale"] = current_sha is None or it["input_binding"].get("scene_plan_sha256") != current_sha
        return {"job_id": job_id, "language": language, "current_scene_plan_sha256": current_sha, "items": items}

    def export_visual_timeline(self, job_id: str, owner_id: str, language: str) -> dict:
        """Newest SUCCEEDED timeline still bound to the current approved plan."""
        status = self.visual_status(job_id, owner_id, language)
        for it in reversed(status["items"]):
            if it["status"] == "SUCCEEDED" and not it["stale"]:
                return self.read_integration_body(job_id, owner_id, language, VISUAL_KIND, it["version"])
        raise IntegrationStale("No current visual timeline exists for the approved scene plan")


def _count(values) -> dict:
    out: dict = {}
    for v in values:
        out[v or "UNKNOWN"] = out.get(v or "UNKNOWN", 0) + 1
    return out
