"""Studio Phase 2: Voice Lab -- capabilities, short previews and approved voice profiles.

Stored as two ``integration_artifacts`` kinds (no new tables):

* ``voice_preview`` -- one short synthesis of a provider/voice/controls
  configuration. Bytes are SHA-256 verified and stored; the body records the
  provider, model and voice the module actually used and which controls its
  metadata confirmed. An identical configuration + text replays the stored
  preview instead of calling the provider again.
* ``voice_profile`` -- a versioned voice configuration for one job/language.
  Created AWAITING_REVIEW; approval is explicit, needs a note, and is refused
  unless a successful, non-mock preview of the exact same configuration
  exists. Approving supersedes the previously approved version.

Honesty rules: only providers declared for the language are offered; controls
are limited to the keys and ranges the provider itself reports; unsupported
capabilities (emotion, pauses, emphasis, voice cloning) are listed rather than
silently accepted. Preview text is prepared exactly like full narration
(``tts_text``), so the preview is representative of the real run.
"""
from __future__ import annotations

import hashlib
import json
import math

from defence_studio.integrations.audio_module import AudioModuleError, provider_languages
from defence_studio.services import tts_text
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from defence_studio.services.script import _now

PREVIEW_KIND = "voice_preview"
PROFILE_KIND = "voice_profile"
PREVIEW_SCHEMA = "defence-studio.voice-preview.v1"
PREVIEW_MAX_CHARS = 400
CONTROL_KEYS = ("rate_wpm", "volume", "pitch_semitones")
UNSUPPORTED = {
    "emotion": "No configured provider has native emotional TTS; rate/volume only.",
    "pauses": "Pause length follows punctuation; requested scene pauses are applied by the renderer.",
    "emphasis": "No guaranteed word emphasis in any configured provider.",
    "voice_cloning": "No configured provider implements voice cloning; none is offered.",
}


def config_sha256(provider: str, voice_id: str, controls: dict) -> str:
    data = json.dumps({"provider": provider, "voice_id": voice_id, "controls": controls},
                      sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def approved_profile(rows: list[dict]) -> dict | None:
    """The APPROVED ``voice_profile`` row among ``rows`` (at most one is ever approved)."""
    return next((r for r in reversed(rows) if r["status"] == "APPROVED"), None)


def _voice_matches(voice: dict, language: str) -> bool:
    return any(str(tag).lower().replace("_", "-").split("-")[0] == language for tag in voice.get("languages", []))


class VoiceLabMixin:
    """Mixed into ``AudioService`` (needs its audio-module client and provider policy)."""

    # -- capabilities ----------------------------------------------------------

    def voice_capabilities(self, language: str) -> list[dict]:
        """Declared providers for ``language`` as the audio module reports them."""
        declared = provider_languages()
        try:
            described = self._client().providers()
        except AudioModuleError as exc:
            raise IntegrationStale(f"Audio module unavailable: {exc.message}") from None
        out = []
        for p in described:
            name = p.get("provider")
            if name not in declared or language not in declared[name]:
                continue
            controls = {k: v for k, v in (p.get("controls") or {}).items() if k in CONTROL_KEYS}
            out.append({"provider": name, "model": p.get("model"), "available": bool(p.get("available")),
                        "error": p.get("error"), "network": bool(p.get("network")), "notes": p.get("notes", ""),
                        "capabilities": p.get("capabilities", []), "controls": controls,
                        "voices": [v for v in p.get("voices", []) if _voice_matches(v, language)]})
        return out

    def _validated_config(self, language: str, provider: str, voice_id: str, controls: dict) -> dict:
        self.check_provider_policy(provider, language, allow_mock=False)
        cap = next((c for c in self.voice_capabilities(language) if c["provider"] == provider), None)
        if cap is None or not cap["available"]:
            raise IntegrationValidationError(f"Provider {provider!r} is not available for {language!r}")
        if voice_id not in {v["id"] for v in cap["voices"]}:
            raise IntegrationValidationError(f"Voice {voice_id!r} is not offered by {provider!r} for {language!r}")
        controls = dict(controls or {})
        unknown = set(controls) - set(cap["controls"])
        if unknown:
            raise IntegrationValidationError(f"Unsupported controls for {provider}: {', '.join(sorted(unknown))}")
        for key, value in controls.items():
            low, high = cap["controls"][key]["range"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                    or not low <= value <= high:
                raise IntegrationValidationError(f"{key} must be between {low} and {high}")
        # One numeric form, so 150 and 150.0 are the same configuration (config_sha256).
        return {k: float(v) for k, v in controls.items()}

    # -- reads -------------------------------------------------------------------

    def voice_lab_state(self, job_id: str, owner_id: str, language: str) -> dict:
        self._check_ownership(job_id, owner_id)
        try:
            providers, provider_error = self.voice_capabilities(language), None
        except IntegrationStale as exc:
            providers, provider_error = [], str(exc)
        profiles = self._integration_rows(job_id, language, PROFILE_KIND)
        previews = self._integration_rows(job_id, language, PREVIEW_KIND)
        bodies = {}
        for row in previews:
            if row["status"] == "SUCCEEDED" and row.get("uri"):
                try:
                    bodies[row["version"]] = self._read_json_artifact(job_id, row)
                except Exception:
                    pass
        approved = approved_profile(profiles)
        return {"job_id": job_id, "language": language, "providers": providers, "provider_error": provider_error,
                "unsupported": UNSUPPORTED, "preview_max_chars": PREVIEW_MAX_CHARS,
                "sample_text": self._sample_text(job_id, owner_id, language),
                "profiles": profiles, "approved_profile": approved,
                "previews": [{**row, "result": bodies.get(row["version"])} for row in previews]}

    def _sample_text(self, job_id: str, owner_id: str, language: str) -> str:
        """The approved scene plan's first scene narration, if any."""
        try:
            _, plan = self._approved_plan(job_id, owner_id, language)
        except Exception:
            return ""
        text = " ".join(s["narration"] for s in plan["scenes"][:1])
        return text[:PREVIEW_MAX_CHARS]

    # -- previews ------------------------------------------------------------------

    def create_voice_preview(self, job_id: str, owner_id: str, language: str, idempotency_key: str, *,
                             provider: str, voice_id: str, controls: dict | None, text: str) -> dict:
        self._check_ownership(job_id, owner_id)
        text = (text or "").strip()
        if not text or len(text) > PREVIEW_MAX_CHARS:
            raise IntegrationValidationError(f"Preview text must be 1 to {PREVIEW_MAX_CHARS} characters")
        controls = self._validated_config(language, provider, voice_id, controls)
        binding = {"provider": provider, "voice_id": voice_id, "controls": controls, "language": language,
                   "config_sha256": config_sha256(provider, voice_id, controls),
                   "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        item, created = self._create_row(job_id, owner_id, language, PREVIEW_KIND, binding, idempotency_key)
        if created:  # short text: synthesized in the request, bounded by the client timeout
            self._run_preview(job_id, language, item["version"], binding, text)
        row = self.get_integration(job_id, owner_id, language, PREVIEW_KIND, item["version"])
        result = self._read_json_artifact(job_id, row) if row["status"] == "SUCCEEDED" else None
        return {**row, "result": result, "replayed": not created}

    def _run_preview(self, job_id: str, language: str, version: int, binding: dict, text: str) -> None:
        try:
            self._transition(job_id, language, PREVIEW_KIND, version, "RUNNING", detail="Synthesizing preview",
                             allowed_from=("QUEUED",))
            prepared = tts_text.prepare(text, language, tts_text.load_lexicon() if language == "ta" else None)
            result = self._client().synthesize(prepared["spoken"], provider=binding["provider"], language=language,
                                               voice_id=binding["voice_id"], controls=binding["controls"])
            data = result["audio_bytes"]
            sha = hashlib.sha256(data).hexdigest()
            if sha != result["audio_sha256"]:
                raise AudioModuleError("integrity", "Audio bytes changed in transit")
            meta = result.get("provider_metadata") or {}
            returned = meta.get("controls") or {}
            applied = {k: v for k, v in binding["controls"].items() if returned.get(k) == v}
            if result.get("validation_status") != "PASSED" or not result.get("duration"):
                raise AudioModuleError("audio_invalid", f"Preview failed validation ({result.get('validation_status')})")
            if result.get("provider") != binding["provider"]:
                raise AudioModuleError("provider_mismatch",
                                       f"Module used {result.get('provider')!r}, not {binding['provider']!r}")
            audio_uri = f"jobs/{job_id}/voice-lab/{language}/{sha}.wav"
            self.store.write_bytes(job_id, audio_uri, data, "audio/wav")
            body = {"schema_version": PREVIEW_SCHEMA, "job_id": job_id, "language": language, "version": version,
                    "created_at": _now(), "binding": binding, "text": text, "tts_text": prepared["spoken"],
                    "text_preparation": {k: prepared[k] for k in ("replacements", "warnings", "lexicon_sha256")},
                    "provider": result.get("provider"), "model": result.get("model"), "voice": meta.get("voice"),
                    "is_mock": bool(meta.get("is_mock")), "prosody": meta.get("prosody"),
                    "applied_controls": applied,
                    "unconfirmed_controls": sorted(k for k in binding["controls"] if k not in applied),
                    "audio_uri": audio_uri, "audio_sha256": sha, "size_bytes": len(data),
                    "duration_seconds": result.get("duration"), "sample_rate": result.get("sample_rate"),
                    "channels": result.get("channels"), "generated_id": result.get("generated_id")}
            uri, body_sha = self._store_json(job_id, f"voice-lab/{language}", body)
            self._transition(job_id, language, PREVIEW_KIND, version, "SUCCEEDED",
                             detail=f"{body['duration_seconds']}s", progress=1.0, uri=uri, sha256=body_sha)
        except AudioModuleError as exc:
            self._transition(job_id, language, PREVIEW_KIND, version, "FAILED", detail=f"{exc.code}: {exc.message}")
        except Exception as exc:
            self._transition(job_id, language, PREVIEW_KIND, version, "FAILED",
                             detail=f"internal: {type(exc).__name__}: {exc}")

    def voice_preview_audio(self, job_id: str, owner_id: str, language: str, version: int) -> bytes:
        body = self.read_integration_body(job_id, owner_id, language, PREVIEW_KIND, version)
        data = self.store.read_bytes(job_id, body["audio_uri"])
        if hashlib.sha256(data).hexdigest() != body["audio_sha256"]:
            raise IntegrationStale("Stored preview audio failed its integrity check")
        return data

    # -- profiles --------------------------------------------------------------------

    def create_voice_profile(self, job_id: str, owner_id: str, language: str, idempotency_key: str, *,
                             provider: str, voice_id: str, controls: dict | None) -> dict:
        self._check_ownership(job_id, owner_id)
        controls = self._validated_config(language, provider, voice_id, controls)
        binding = {"provider": provider, "voice_id": voice_id, "controls": controls, "language": language,
                   "config_sha256": config_sha256(provider, voice_id, controls)}
        item, created = self._create_row(job_id, owner_id, language, PROFILE_KIND, binding, idempotency_key)
        if created:
            self._transition(job_id, language, PROFILE_KIND, item["version"], "AWAITING_REVIEW",
                             detail="Awaiting approval", actor=owner_id, allowed_from=("QUEUED",))
        return self.get_integration(job_id, owner_id, language, PROFILE_KIND, item["version"])

    def review_voice_profile(self, job_id: str, owner_id: str, language: str, version: int,
                             decision: str, note: str) -> dict:
        note = (note or "").strip()
        if not note:
            raise IntegrationValidationError("A note is required")
        if decision not in ("APPROVED", "REJECTED"):
            raise IntegrationValidationError("decision must be APPROVED or REJECTED")
        row = self.get_integration(job_id, owner_id, language, PROFILE_KIND, version)
        if row["status"] != "AWAITING_REVIEW":
            raise IntegrationStale(f"Voice profile v{version} is {row['status']}, not AWAITING_REVIEW")
        if decision == "APPROVED":
            sha = row["input_binding"]["config_sha256"]
            heard = [p for p in self._integration_rows(job_id, language, PREVIEW_KIND)
                     if p["status"] == "SUCCEEDED" and p["input_binding"]["config_sha256"] == sha]
            if not any(not self._read_json_artifact(job_id, p)["is_mock"] for p in heard):
                raise IntegrationStale("Generate and listen to a preview of this exact configuration before approving it")
            previous = approved_profile(self._integration_rows(job_id, language, PROFILE_KIND))
            if previous is not None:
                self._transition(job_id, language, PROFILE_KIND, previous["version"], "SUPERSEDED",
                                 detail=f"Superseded by v{version}", actor=owner_id, allowed_from=("APPROVED",))
        self._transition(job_id, language, PROFILE_KIND, version, decision, detail=note[:500], actor=owner_id,
                         allowed_from=("AWAITING_REVIEW",))
        return self.get_integration(job_id, owner_id, language, PROFILE_KIND, version)

    def approved_voice_profile(self, job_id: str, language: str) -> dict | None:
        row = approved_profile(self._integration_rows(job_id, language, PROFILE_KIND))
        if row is None:
            return None
        return {"version": row["version"], **row["input_binding"]}
