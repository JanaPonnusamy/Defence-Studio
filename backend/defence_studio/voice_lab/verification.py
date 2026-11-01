"""Speaker verification (T0-D): owner similarity, reference-narrator similarity, identity-leakage check.

A verifier scores how much a candidate narration sounds like (a) the owner and (b) the reference
narrator whose *delivery* was borrowed. Identity transfer is acceptable only when the candidate is
clearly closer to the owner than to the reference.

No speaker-verification model is installed or approved. The only provider is ``UnavailableVerifier``
(no scores). ``judge`` turns scores into verdicts and never returns PASS unless the model has been
validated on real recordings *and* calibrated thresholds are supplied. Scores are never invented:
missing scores give NOT_EVALUATED.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

CONTRACT = "defence-studio.voice-lab.speaker-verification.v1"
IDENTITY_VERDICTS = ("NOT_EVALUATED", "UNCALIBRATED", "PASS", "FAIL")
LEAKAGE_VERDICTS = ("NOT_EVALUATED", "UNCALIBRATED", "NO_LEAKAGE_DETECTED", "LEAKAGE_SUSPECTED")


@dataclass(frozen=True)
class VerificationRequest:
    candidate_wav: Path                       # narration to check (after conversion and/or delivery DSP)
    owner_enrolment: tuple[Path, ...]         # owner's own consented recordings
    reference_wav: Path | None = None         # the delivery reference narrator (leakage check)
    config: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Calibration:
    """Thresholds measured for one model on held-out recordings. There are no built-in defaults."""
    model_id: str
    owner_min_similarity: float               # candidate must reach this similarity to the owner
    min_owner_minus_reference: float          # and be at least this much closer to owner than to reference
    validated: bool                           # True only after the model was tested on real recordings
    evidence_ref: str                         # where the calibration evidence is stored


@dataclass
class VerificationResult:
    status: str                               # SCORED | UNAVAILABLE | FAILED
    provider_id: str
    owner_similarity: float | None = None
    reference_similarity: float | None = None
    model: dict | None = None
    processing_s: float | None = None
    errors: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


class SpeakerVerifier(Protocol):
    provider_id: str

    def describe(self) -> dict: ...

    def score(self, request: VerificationRequest) -> VerificationResult: ...


class UnavailableVerifier:
    provider_id = "none"

    def describe(self) -> dict:
        return {"provider_id": self.provider_id, "available": False, "model": None,
                "reason": "No speaker-verification model is installed or approved (owner approval required)."}

    def score(self, request: VerificationRequest) -> VerificationResult:
        return VerificationResult(status="UNAVAILABLE", provider_id=self.provider_id,
                                  errors=[{"code": "SPEAKER_VERIFICATION_UNAVAILABLE",
                                           "detail": "No speaker-verification model is installed or approved."}],
                                  limitations=["Owner similarity and reference leakage were not measured."])


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def judge(result: VerificationResult, calibration: Calibration | None) -> dict:
    """Verdicts from measured scores. PASS requires SCORED + finite scores + a validated calibration
    for the same model. Without calibration the scores are reported but the verdict is UNCALIBRATED."""
    out = {"contract": CONTRACT, "status": result.status, "provider_id": result.provider_id,
           "owner_similarity": result.owner_similarity, "reference_similarity": result.reference_similarity,
           "margin": None, "identity": "NOT_EVALUATED", "leakage": "NOT_EVALUATED", "reasons": [],
           "model": result.model, "errors": result.errors, "limitations": list(result.limitations)}
    if result.status != "SCORED" or not _finite(result.owner_similarity):
        out["reasons"].append("no measured owner similarity")
        return out
    has_ref = _finite(result.reference_similarity)
    if has_ref:
        out["margin"] = round(result.owner_similarity - result.reference_similarity, 6)
    model_id = (result.model or {}).get("model_id")
    if calibration is None or not calibration.validated or calibration.model_id != model_id:
        out["identity"] = "UNCALIBRATED"
        if has_ref:
            out["leakage"] = "UNCALIBRATED"
        out["reasons"].append("no validated calibration for this model; thresholds unknown")
        return out
    owner_ok = result.owner_similarity >= calibration.owner_min_similarity
    if has_ref:
        leak = out["margin"] < calibration.min_owner_minus_reference
        out["leakage"] = "LEAKAGE_SUSPECTED" if leak else "NO_LEAKAGE_DETECTED"
    else:
        out["reasons"].append("no reference score; leakage not evaluated")
    out["identity"] = "PASS" if owner_ok and out["leakage"] == "NO_LEAKAGE_DETECTED" else "FAIL"
    out["limitations"].append("Automatic similarity is not a listening test; native-listener review is still required.")
    return out


__all__ = ["CONTRACT", "VerificationRequest", "Calibration", "VerificationResult", "SpeakerVerifier",
           "UnavailableVerifier", "judge"]
