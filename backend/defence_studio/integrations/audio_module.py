"""HTTP adapter for the standalone Audio Module (public API only).

Public endpoints used (``standalone/audio-module/docs/CONTRACTS.md``):
``GET /health``, ``GET /tts/providers``, ``POST /tts/synthesize``,
``GET /jobs/{id}``, ``GET /generated/{id}`` + ``/file``, ``POST /references``,
``POST /references/{id}/process``, ``GET /references/{id}``,
``GET /alignment/{id}``. Internal tables, storage paths and analyzer classes
are never touched.

Capability facts are enforced here, not assumed: the module's local engine
exposes only OS voices (no Tamil voice is installed on the audited machine),
so a provider is only used for a language it is *declared* to support via
``DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES`` (default ``local-pyttsx3:en``).
"""
from __future__ import annotations

import hashlib
import os
import time

import httpx

DEFAULT_PROVIDER_LANGUAGES = {"local-pyttsx3": ("en",), "mock-tts": ()}


class AudioModuleError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def configured_base_url() -> str:
    return os.environ.get("DEFENCE_STUDIO_AUDIO_MODULE_URL", "http://127.0.0.1:8001").rstrip("/")


def provider_languages() -> dict[str, tuple[str, ...]]:
    """Declared provider -> languages. ``a:en|ta,b:en`` in the environment."""
    raw = os.environ.get("DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES")
    if not raw:
        return dict(DEFAULT_PROVIDER_LANGUAGES)
    out: dict[str, tuple[str, ...]] = {}
    for part in raw.split(","):
        name, _, langs = part.partition(":")
        if name.strip():
            out[name.strip()] = tuple(l for l in langs.strip().split("|") if l)
    return out


class AudioModuleClient:
    def __init__(self, base_url: str | None = None, *, client_factory=None, poll_interval: float = 0.5,
                 job_timeout_seconds: float = 180.0, request_timeout: float = 60.0):
        self.base_url = (base_url or configured_base_url()).rstrip("/")
        self._factory = client_factory or (lambda **kw: httpx.Client(**kw))
        self.poll_interval, self.job_timeout, self.request_timeout = poll_interval, job_timeout_seconds, request_timeout

    # -- transport ------------------------------------------------------------

    def _call(self, client, method, path, *, raw=False, **kw):
        try:
            response = client.request(method, f"{self.base_url}{path}", **kw)
        except httpx.TimeoutException as exc:
            raise AudioModuleError("timeout", f"Audio module timed out on {path}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AudioModuleError("unreachable", f"Audio module unreachable at {self.base_url}: {exc}",
                                   retryable=True) from exc
        if response.status_code >= 400:
            try:
                err = (response.json().get("error") or {})
            except ValueError:
                err = {}
            code = err.get("code") or f"http_{response.status_code}"
            raise AudioModuleError(code, f"{code}: {err.get('message') or response.text[:300]}",
                                   retryable=response.status_code >= 500)
        if raw:
            return response.content
        try:
            return response.json()
        except ValueError as exc:
            raise AudioModuleError("malformed_response", f"Non-JSON response from {path}") from exc

    def _wait(self, client, job: dict, what: str, on_tick=None) -> dict:
        deadline = time.monotonic() + self.job_timeout
        while job.get("status") not in ("SUCCEEDED", "FAILED", "CANCELLED"):
            if time.monotonic() > deadline:
                raise AudioModuleError("job_timeout", f"{what} did not finish in time", retryable=True)
            time.sleep(self.poll_interval)
            job = self._call(client, "GET", f"/jobs/{job['job_id']}")
            if on_tick:
                on_tick()
        if job["status"] != "SUCCEEDED":
            raise AudioModuleError(job.get("error_code") or "job_failed",
                                   job.get("error_message") or f"{what} job {job['status']}")
        return job

    # -- operations -----------------------------------------------------------

    def health(self) -> dict:
        with self._factory(timeout=self.request_timeout) as client:
            return self._call(client, "GET", "/health")

    def providers(self) -> list[dict]:
        with self._factory(timeout=self.request_timeout) as client:
            return self._call(client, "GET", "/tts/providers").get("providers", [])

    def synthesize(self, text: str, *, provider: str, language: str, voice_id: str | None = None,
                   controls: dict | None = None) -> dict:
        """Returns ``{generated_id, duration, sample_rate, channels, validation_status,
        provider, model, provider_metadata, audio_bytes, audio_sha256}``. The bytes are
        hashed here so callers never trust an unverified file."""
        with self._factory(timeout=self.request_timeout) as client:
            body = {"text": text, "provider": provider, "language": language, "voice_id": voice_id}
            if controls:
                body["controls"] = controls
            job = self._call(client, "POST", "/tts/synthesize", json=body)
            job = self._wait(client, job, "TTS synthesis")
            meta = self._call(client, "GET", f"/generated/{job['result_id']}")
            data = self._call(client, "GET", f"/generated/{job['result_id']}/file", raw=True)
        if not data:
            raise AudioModuleError("empty_audio", "Audio module returned an empty file")
        return {**meta, "generated_id": job["result_id"], "audio_bytes": data,
                "audio_sha256": hashlib.sha256(data).hexdigest()}

    def align(self, audio_bytes: bytes, text: str, language: str, title: str) -> dict:
        """Register the *generated* audio + its script text as a reference owned
        by this system, process it, and return the module's alignment verbatim."""
        import json as _json
        with self._factory(timeout=self.request_timeout) as client:
            ref = self._call(client, "POST", "/references",
                             data={"title": title[:200], "language": language,
                                   "rights_metadata": _json.dumps({"authorized": True, "notes": "synthesized by this system"}),
                                   "transcript_format": "plain", "transcript_text": text},
                             files={"audio_file": ("scene.wav", audio_bytes, "audio/wav")})
            job = self._call(client, "POST", f"/references/{ref['reference_id']}/process")
            self._wait(client, job, "Alignment")
            ref = self._call(client, "GET", f"/references/{ref['reference_id']}")
            if not ref.get("alignment_id"):
                raise AudioModuleError("no_alignment", "Processing finished without an alignment")
            return self._call(client, "GET", f"/alignment/{ref['alignment_id']}")
