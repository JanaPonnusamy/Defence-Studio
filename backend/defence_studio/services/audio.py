"""RM-4: approved scene plan -> real narration audio + alignment artifact.

Per scene, the Audio Module synthesizes the scene's approved narration; the
core verifies and stores the bytes (SHA-256, measured duration from the
module's decode), then asks the module to align the generated audio against
its script text. The persisted ``defence-studio.audio-set.v1`` artifact is
bound to the exact approved scene plan.

Honesty rules enforced here:
* A provider is only used for a language it is *declared* to support
  (``provider_languages()``); Tamil on an English-only voice fails closed.
* The mock provider never produces production narration: it needs an explicit
  ``allow_mock`` AND ``DEFENCE_STUDIO_ALLOW_MOCK_TTS=1``, and is flagged
  ``is_mock`` so QC/render approval can refuse it.
* Word-level timing is the module's approximation (its own ``quality_label``
  and confidence are carried through); scene-level durations are measured from
  the actual audio. Supported rate/pitch directions are passed through the
  documented API and marked applied only when provider metadata confirms them.
  Tone remains unsupported, and requested pauses are applied by the renderer.
"""
from __future__ import annotations

import hashlib
import math
import os

from defence_studio.integrations.audio_module import AudioModuleClient, AudioModuleError, provider_languages
from defence_studio.services.integration import (
    IntegrationService, IntegrationStale, IntegrationValidationError,
)
from defence_studio.services.script import _now

AUDIO_KIND = "audio_set"
AUDIO_SCHEMA = "defence-studio.audio-set.v1"
# Audio-module providers whose rate/pitch controls are applied natively.
NATIVE_CONTROL_PROVIDERS = ("local-pyttsx3", "edge-neural")


class AudioService(IntegrationService):
    def __init__(self, database_file, storage_root, *, provider=None, audio_client=None):
        super().__init__(database_file, storage_root, provider=provider)
        self._audio_client = audio_client

    def _client(self) -> AudioModuleClient:
        return self._audio_client or AudioModuleClient()

    @staticmethod
    def _tts_controls(scene: dict, provider: str) -> tuple[dict, list[str]]:
        """Map only verified local-engine controls; surface the rest explicitly."""
        direction = scene.get("voice_direction") or {}
        controls, unsupported = {}, ["tone"]
        rate = float(direction.get("rate", 1.0))
        if rate != 1.0:
            rate_wpm = round(170 * rate)
            if provider in NATIVE_CONTROL_PROVIDERS and 80 <= rate_wpm <= 260:
                controls["rate_wpm"] = rate_wpm
            else:
                unsupported.append("rate")
        pitch = float(direction.get("pitch", 1.0))
        if pitch != 1.0:
            pitch_semitones = round(12 * math.log2(pitch), 2)
            if provider in NATIVE_CONTROL_PROVIDERS and -3 <= pitch_semitones <= 3:
                controls["pitch_semitones"] = pitch_semitones
            else:
                unsupported.append("pitch")
        return controls, unsupported

    # -- policy ---------------------------------------------------------------

    @staticmethod
    def check_provider_policy(provider: str, language: str, allow_mock: bool) -> None:
        declared = provider_languages()
        if provider == "mock-tts":
            if not (allow_mock and os.environ.get("DEFENCE_STUDIO_ALLOW_MOCK_TTS") == "1"):
                raise IntegrationValidationError(
                    "The mock TTS provider produces a placeholder tone, not narration. It requires "
                    "allow_mock=true and DEFENCE_STUDIO_ALLOW_MOCK_TTS=1 and its output is never accepted for render.")
            return
        if provider not in declared:
            raise IntegrationValidationError(f"Unknown TTS provider {provider!r}; declared: {sorted(declared)}")
        if language not in declared[provider]:
            raise IntegrationValidationError(
                f"LANGUAGE_UNSUPPORTED: provider {provider!r} is not declared to support {language!r} "
                f"(declared: {list(declared[provider]) or 'none'}). Install/declare a voice that supports it "
                "(DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES) rather than synthesizing with a wrong-language voice.")

    # -- operations -----------------------------------------------------------

    def start_audio(self, job_id: str, owner_id: str, language: str, idempotency_key: str, *,
                    provider: str = "local-pyttsx3", voice_id: str | None = None, align: bool = True,
                    allow_mock: bool = False, background: bool = True) -> dict:
        row, plan = self._approved_plan(job_id, owner_id, language)
        self.check_provider_policy(provider, language, allow_mock)
        binding = {"scene_plan_sha256": row["plan_sha256"], "scene_plan_version": row["version"],
                   "script_revision_sha256": row["script_revision_sha256"], "provider": provider,
                   "voice_id": voice_id, "align": bool(align), "language": language}
        item, created = self._create_row(job_id, owner_id, language, AUDIO_KIND, binding, idempotency_key)
        if created:
            self._launch(lambda: self._run(job_id, language, item["version"], plan, binding), background)
        return self.get_integration(job_id, owner_id, language, AUDIO_KIND, item["version"])

    def retry_audio(self, job_id: str, owner_id: str, language: str, version: int, idempotency_key: str, *,
                    background: bool = True) -> dict:
        old = self.get_integration(job_id, owner_id, language, AUDIO_KIND, version)
        if old["status"] not in ("FAILED", "INTERRUPTED", "CANCELLED"):
            raise IntegrationStale(f"Only a FAILED/INTERRUPTED/CANCELLED run can be retried (is {old['status']})")
        row, plan = self._approved_plan(job_id, owner_id, language)
        binding = dict(old["input_binding"])
        if binding["scene_plan_sha256"] != row["plan_sha256"]:
            raise IntegrationStale("The scene plan changed since that run; start new audio instead")
        item, created = self._create_row(job_id, owner_id, language, AUDIO_KIND, binding, idempotency_key)
        if created:
            self._launch(lambda: self._run(job_id, language, item["version"], plan, binding), background)
        return self.get_integration(job_id, owner_id, language, AUDIO_KIND, item["version"])

    def _run(self, job_id: str, language: str, version: int, plan: dict, binding: dict) -> None:
        try:
            self._transition(job_id, language, AUDIO_KIND, version, "RUNNING", detail="Starting synthesis",
                             progress=0.02, allowed_from=("QUEUED",))
            client = self._client()
            scenes, total = [], len(plan["scenes"])
            for index, scene in enumerate(plan["scenes"]):
                self._transition(job_id, language, AUDIO_KIND, version, "RUNNING",
                                 detail=f"Synthesizing scene {index + 1}/{total}",
                                 progress=round(0.05 + 0.9 * index / total, 3))
                controls, unsupported = self._tts_controls(scene, binding["provider"])
                result = client.synthesize(scene["narration"], provider=binding["provider"],
                                           language=language, voice_id=binding.get("voice_id"), controls=controls)
                sha = hashlib.sha256(result["audio_bytes"]).hexdigest()
                if sha != result["audio_sha256"]:
                    raise AudioModuleError("integrity", "Audio bytes changed in transit")
                uri = f"jobs/{job_id}/audio/{language}/{sha}.wav"
                self.store.write_bytes(job_id, uri, result["audio_bytes"], "audio/wav")
                meta = result.get("provider_metadata") or {}
                returned_controls = meta.get("controls") or {}
                applied_controls = {key: value for key, value in controls.items()
                                    if returned_controls.get(key) == value}
                unsupported.extend(key for key in controls if key not in applied_controls)
                entry = {
                    "scene_id": scene["scene_id"], "order": scene["order"], "text": scene["narration"],
                    "generated_id": result["generated_id"], "audio_uri": uri, "audio_sha256": sha,
                    "size_bytes": len(result["audio_bytes"]), "duration_seconds": result.get("duration"),
                    "sample_rate": result.get("sample_rate"), "channels": result.get("channels"),
                    "validation_status": result.get("validation_status"),
                    "validation_details": result.get("validation_details") or {},
                    "provider": result.get("provider"), "model": result.get("model"),
                    "voice": meta.get("voice"), "is_mock": bool(meta.get("is_mock")),
                    "planned_seconds": scene["planned_timing"]["estimated_seconds"],
                    "voice_direction": {"requested": scene.get("voice_direction"),
                                        "applied": bool(applied_controls),
                                        "applied_controls": applied_controls,
                                        "unsupported_controls": sorted(set(unsupported)),
                                        "note": "Rate/pitch are applied only when confirmed by provider metadata; "
                                                "tone is not implemented, and pause_after_ms is applied by the renderer."},
                    "alignment": None,
                }
                if not entry["duration_seconds"] or entry["validation_status"] != "PASSED":
                    raise AudioModuleError("audio_invalid", f"Scene {scene['scene_id']}: audio failed validation "
                                           f"({entry['validation_status']})")
                if binding.get("align"):
                    al = client.align(result["audio_bytes"], scene["narration"], language,
                                      f"{job_id}:{scene['scene_id']}")
                    entry["alignment"] = {
                        "alignment_id": al.get("alignment_id"), "provider": al.get("provider"),
                        "quality_label": al.get("quality_label"), "mean_confidence": al.get("mean_confidence"),
                        "is_approximate": True, "segments": al.get("segments", []),
                    }
                scenes.append(entry)
            body = {
                "schema_version": AUDIO_SCHEMA, "job_id": job_id, "language": language, "version": version,
                "binding": binding, "created_at": _now(), "scenes": scenes,
                "summary": {
                    "scene_count": len(scenes),
                    "total_duration_seconds": round(sum(s["duration_seconds"] for s in scenes), 3),
                    "planned_total_seconds": round(sum(s["planned_seconds"] for s in scenes), 3),
                    "any_mock": any(s["is_mock"] for s in scenes),
                    "aligned_scene_count": sum(1 for s in scenes if s["alignment"]),
                },
                "cost": {"tts_cost_inr": 0.0, "is_measured": binding["provider"] == "local-pyttsx3",
                         "note": "Local OS speech engine: no paid API call. Other providers must report their own cost."},
            }
            uri, sha = self._store_json(job_id, f"audio-sets/{language}", body)
            self._transition(job_id, language, AUDIO_KIND, version, "SUCCEEDED",
                             detail=f"{len(scenes)} scene(s), {body['summary']['total_duration_seconds']}s",
                             progress=1.0, uri=uri, sha256=sha)
        except AudioModuleError as exc:
            self._transition(job_id, language, AUDIO_KIND, version, "FAILED", detail=f"{exc.code}: {exc.message}")
        except Exception as exc:
            self._transition(job_id, language, AUDIO_KIND, version, "FAILED",
                             detail=f"internal: {type(exc).__name__}: {exc}")

    # -- reads / verification -------------------------------------------------

    def audio_status(self, job_id: str, owner_id: str, language: str) -> dict:
        self._check_ownership(job_id, owner_id)
        try:
            current, _ = self._approved_plan(job_id, owner_id, language)
            current_sha = current["plan_sha256"]
        except Exception:
            current_sha = None
        items = self._integration_rows(job_id, language, AUDIO_KIND)
        for it in items:
            it["stale"] = current_sha is None or it["input_binding"].get("scene_plan_sha256") != current_sha
        providers = {name: list(langs) for name, langs in provider_languages().items()}
        return {"job_id": job_id, "language": language, "current_scene_plan_sha256": current_sha,
                "items": items, "provider_languages": providers}

    def verify_audio_set(self, job_id: str, owner_id: str, language: str, version: int) -> dict:
        """Re-hash every stored scene file against the artifact (never trust)."""
        body = self.read_integration_body(job_id, owner_id, language, AUDIO_KIND, version)
        problems = []
        for s in body["scenes"]:
            try:
                if self.store.sha256_of(job_id, s["audio_uri"]) != s["audio_sha256"]:
                    problems.append(f"{s['scene_id']}: checksum mismatch")
            except Exception as exc:
                problems.append(f"{s['scene_id']}: unreadable ({type(exc).__name__})")
        return {"ok": not problems, "problems": problems, "scene_count": len(body["scenes"])}

    def current_audio_set(self, job_id: str, owner_id: str, language: str) -> dict:
        status = self.audio_status(job_id, owner_id, language)
        for it in reversed(status["items"]):
            if it["status"] == "SUCCEEDED" and not it["stale"]:
                return self.read_integration_body(job_id, owner_id, language, AUDIO_KIND, it["version"])
        raise IntegrationStale("No current audio exists for the approved scene plan")
