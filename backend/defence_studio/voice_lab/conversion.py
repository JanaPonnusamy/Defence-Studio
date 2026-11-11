"""Owner-identity layer (T0-C): voice-conversion interface.

Converts already-generated narration audio (any narrator/model) into the owner's voice, using only
the owner's own consented recordings. This is the stage that runs *before* reference-delivery DSP
(style.apply_style): identity first, then delivery.

No voice-conversion model is installed or approved. The only provider is ``UnavailableConverter``,
which always answers ``UNAVAILABLE``. ``run_conversion`` is the single entry point: it checks the
request and consent, times the provider, and refuses any ``CONVERTED`` result that does not name a
real model or does not produce a readable output file, so a placeholder can never pass as cloning.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol

CONTRACT = "defence-studio.voice-lab.conversion.v1"
STATUSES = ("CONVERTED", "UNAVAILABLE", "FAILED")
REQUIRED_CONSENT_SCOPE = "GENERATION"  # the owner's voice is used to produce new speech
_MODEL_KEYS = ("model_id", "weights_revision", "code_commit", "licence")


@dataclass(frozen=True)
class OwnerVoice:
    """The authorised identity target. Audio must be the owner's own recordings, never a third party's."""
    narrator_id: str
    consent_id: str
    dataset_version_id: str | None = None          # approved Voice Lab dataset version (preferred)
    reference_paths: tuple[Path, ...] = ()         # or explicit owner recordings (local files only)


@dataclass(frozen=True)
class ConversionRequest:
    source_wav: Path                               # generated narration to convert
    owner: OwnerVoice
    config: Mapping = field(default_factory=dict)  # provider-specific; recorded in provenance


@dataclass
class ConversionResult:
    status: str
    provider_id: str
    output_wav: Path | None = None
    model: dict | None = None                      # {model_id, weights_revision, code_commit, licence}
    processing_s: float | None = None
    quality: dict = field(default_factory=dict)    # measured values only; empty unless CONVERTED
    errors: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"contract": CONTRACT, "status": self.status, "provider_id": self.provider_id,
                "output_wav": str(self.output_wav) if self.output_wav else None, "model": self.model,
                "processing_s": self.processing_s, "quality": self.quality, "errors": self.errors,
                "limitations": self.limitations}


class VoiceConverter(Protocol):
    provider_id: str

    def describe(self) -> dict: ...

    def convert(self, request: ConversionRequest, out_wav: Path) -> ConversionResult: ...


class UnavailableConverter:
    """Explicit 'no model' provider. It never produces audio."""
    provider_id = "none"

    def describe(self) -> dict:
        return {"provider_id": self.provider_id, "available": False, "model": None,
                "reason": "No voice-conversion model is installed or approved (owner approval required)."}

    def convert(self, request: ConversionRequest, out_wav: Path) -> ConversionResult:
        return ConversionResult(
            status="UNAVAILABLE", provider_id=self.provider_id,
            errors=[{"code": "VOICE_CONVERSION_UNAVAILABLE",
                     "detail": "No voice-conversion model is installed or approved."}],
            limitations=["Owner voice identity is not implemented; narration stays in the base model's voice."])


def _fail(provider_id: str, code: str, detail: str, **extra) -> ConversionResult:
    return ConversionResult(status="FAILED", provider_id=provider_id, errors=[{"code": code, "detail": detail}], **extra)


def run_conversion(converter: VoiceConverter, request: ConversionRequest, out_wav: Path, *,
                   require_consent: Callable[[str, str], object]) -> ConversionResult:
    """Validate, run and audit one conversion. ``require_consent(consent_id, scope)`` must raise if the
    owner's consent does not cover ``REQUIRED_CONSENT_SCOPE`` (Narrators.require_consent fits)."""
    pid = getattr(converter, "provider_id", "unknown")
    if not Path(request.source_wav).is_file():
        return _fail(pid, "SOURCE_MISSING", f"source audio not found: {request.source_wav}")
    if not request.owner.dataset_version_id and not request.owner.reference_paths:
        return _fail(pid, "OWNER_VOICE_MISSING", "an approved dataset version or owner recordings are required")
    missing = [str(p) for p in request.owner.reference_paths if not Path(p).is_file()]
    if missing:
        return _fail(pid, "OWNER_RECORDING_MISSING", f"owner recordings not found: {missing}")
    try:
        require_consent(request.owner.consent_id, REQUIRED_CONSENT_SCOPE)
    except Exception as exc:  # the consent store decides; any refusal blocks conversion
        return _fail(pid, "CONSENT_NOT_VALID", f"owner consent does not cover {REQUIRED_CONSENT_SCOPE}: {exc}")
    t0 = time.perf_counter()
    try:
        res = converter.convert(request, out_wav)
    except Exception as exc:
        return _fail(pid, "PROVIDER_ERROR", f"{type(exc).__name__}: {exc}"[:500],
                     processing_s=round(time.perf_counter() - t0, 3))
    res.processing_s = round(time.perf_counter() - t0, 3) if res.processing_s is None else res.processing_s
    if res.status not in STATUSES:
        return _fail(pid, "INVALID_STATUS", f"provider returned status {res.status!r}")
    if res.status == "CONVERTED":
        if not res.model or any(not res.model.get(k) for k in _MODEL_KEYS):
            return _fail(pid, "MODEL_IDENTITY_MISSING", f"a converted result must name {', '.join(_MODEL_KEYS)}")
        if not res.output_wav or not Path(res.output_wav).is_file() or Path(res.output_wav).stat().st_size == 0:
            return _fail(pid, "OUTPUT_MISSING", "provider reported CONVERTED but wrote no audio")
        res.limitations.append("Speaker similarity is not verified by conversion itself; run speaker verification.")
    return res


__all__ = ["CONTRACT", "REQUIRED_CONSENT_SCOPE", "OwnerVoice", "ConversionRequest", "ConversionResult",
           "VoiceConverter", "UnavailableConverter", "run_conversion"]
