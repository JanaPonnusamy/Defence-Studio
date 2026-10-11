"""Canonical evidence-package and timestamp rules; no network or AI calls."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PACKAGE_SCHEMA = "defence-studio.evidence-package.v1"
POLICY = "defence-studio.evidence-approval.strict.v1"


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value: str | None) -> dict:
    """Keep the source representation. Never guess a timezone or a day time."""
    result = {"original": value, "utc": None, "precision": "MISSING", "timezone": None}
    if not value:
        return result
    try:
        if len(value) == 10:
            datetime.strptime(value, "%Y-%m-%d")
            return {**result, "precision": "DATE_ONLY"}
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return {**result, "precision": "TIMEZONE_UNKNOWN"}
        return {**result, "utc": dt.astimezone(timezone.utc).isoformat(),
                "precision": "TIMESTAMP", "timezone": dt.tzname()}
    except (ValueError, TypeError, OverflowError):
        return {**result, "precision": "UNRELIABLE"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ApprovalBody(StrictModel):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(min_length=1, max_length=4000)


class ArtifactRef(StrictModel):
    artifact_type: str
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str


class EvidencePackageV1(StrictModel):
    schema_version: Literal["defence-studio.evidence-package.v1"]
    policy: Literal["defence-studio.evidence-approval.strict.v1"]
    job_id: str
    topic: str
    language: str
    generated_from_timestamp: str
    artifacts: list[ArtifactRef]
    discovery: dict
    sources: list[dict]
    claims: list[dict]
    duplicate_groups: list[dict]
    claim_groups: list[dict]
    conflicts: list[dict]
    processing: dict
    audit: dict
    timeline: list[dict]
    limitations: list[str]
    blockers: list[str]
    eligible_claim_ids: list[str]
    ready: bool
    status: Literal["DRAFT", "APPROVED"]
    approval: dict | None


def validate_package(value: dict) -> dict:
    parsed = EvidencePackageV1.model_validate(value).model_dump()
    if parsed["ready"] != (not parsed["blockers"]):
        raise ValueError("Readiness and blockers disagree")
    if parsed["status"] == "APPROVED" and (not parsed["ready"] or not parsed["approval"]):
        raise ValueError("Approved package requires readiness and an approval record")
    if parsed["status"] == "DRAFT" and parsed["approval"] is not None:
        raise ValueError("Draft cannot contain approval")
    return parsed
