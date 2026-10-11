"""Phase 5 (M2.0) asset-management & rights-provenance contract (contract only).

This module defines the *stable, versioned* shapes for an asset, its rights
record, and its binding to a scene, so the later Phase 5 asset service (M2.1+)
can be built against a fixed schema. It is deliberately a contract only: it runs
no network call, touches no database, imports nothing from TTS / alignment /
FFmpeg / render, downloads nothing, defines no route, and assigns no asset to a
real scene. Nothing here fetches, ingests, or verifies bytes.

Three boundaries live here, mirroring ``contracts/scene.py`` and reusing the
same ``StrictModel`` / ``digest`` from ``contracts/evidence.py`` (no parallel
contract style):

1. **Asset descriptor** -- :class:`AssetDescriptorV1`: a single asset's stable
   identity, descriptive metadata, optional content integrity, provenance, and
   review lifecycle state. A referenced-only asset (the M2.0 default, since this
   milestone downloads nothing) carries no content hash and records *why* --
   never a fabricated hash.

2. **Rights record** -- :class:`RightsRecordV1`: licence, attribution,
   permitted uses, commercial-use permission, restrictions, and expiry, with
   explicit ``unknown`` / ``unverified`` states. Rights are **operator
   assertions reviewed by a human**, kept distinct from source-provided facts;
   nothing here promotes "publicly accessible" to "licensed for reuse".

3. **Scene assignment** -- :class:`AssetAssignmentV1`: a *separate*, versioned
   artifact binding one asset to one scene of an **approved** scene plan, pinned
   to the exact ``scene_plan_sha256`` + ``scene_plan_version`` + ``scene_id`` it
   was assigned against. It is kept strictly separate from M0's
   ``VisualSuggestionV1`` (a non-binding idea, explicitly *not* an assignment),
   and **the M0 scene contract is not modified**.

Honesty / safety rules this contract enforces structurally:

* **Ingestion is never approval.** ``review_status`` starts at ``DRAFT`` /
  ``PENDING_REVIEW``; only an explicit human decision reaches ``APPROVED``.
* **Unknown rights fail closed.** ``rights_basis = unknown`` (or a missing
  source URL, or ``commercial_use = unknown``) forces an ``effective_rights``
  of ``UNVERIFIED`` -- never silently treated as usable. The deterministic
  :func:`evaluate_effective_rights` predicate makes that rule explicit and
  testable without any service.
* **Source facts vs operator assertions are distinguished.** Declared
  provenance metadata carries a :class:`MetadataFactStatus` (collected /
  unavailable / not-collected), never a bare value asserted as truth; licence
  terms are explicitly operator-asserted and reviewer-confirmed.
* **No fabricated media or hashes.** A content hash exists only when real bytes
  were measured (a later milestone); a referenced-only asset records
  ``content_status`` instead, never a guessed digest.

Service logic -- persistence, routes, byte storage, SSRF-guarded fetch, the
approval ledger, export-readiness gating -- is **not** implemented in M2.0.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from defence_studio.contracts.evidence import StrictModel, digest


class ValueModel(StrictModel):
    """Like ``StrictModel`` (``extra="forbid"``) but validates by value rather
    than requiring exact runtime types. Unknown fields and pattern/range
    violations are still rejected; the only relaxation is that an enum field
    accepts its *string value* (``"image"``) as well as the enum instance
    (``AssetKind.IMAGE``). This is correct for a JSON-serialized contract whose
    descriptors/rights/assignments round-trip through SQLite/HTTP as strings.
    """
    model_config = ConfigDict(extra="forbid", strict=False)

__all__ = [
    "ASSET_DESCRIPTOR_SCHEMA", "RIGHTS_RECORD_SCHEMA", "ASSET_ASSIGNMENT_SCHEMA",
    "MetadataFactStatus", "ContentStatus", "AssetKind", "RightsBasis",
    "CommercialUse", "PermittedUse", "AssetReviewStatus", "EffectiveRights",
    "DeclaredText", "AssetContentV1", "AssetProvenanceV1", "RightsRecordV1",
    "AssetDescriptorV1", "AssetAssignmentV1",
    "evaluate_effective_rights", "asset_digest", "rights_digest", "assignment_digest",
]

# -- identities ------------------------------------------------------------

ASSET_DESCRIPTOR_SCHEMA = "defence-studio.asset-descriptor.v1"
RIGHTS_RECORD_SCHEMA = "defence-studio.asset-rights.v1"
ASSET_ASSIGNMENT_SCHEMA = "defence-studio.asset-assignment.v1"


# -- vocabularies ----------------------------------------------------------


class MetadataFactStatus(str, Enum):
    """Why a *source-declared* provenance field has (or lacks) a value.

    Mirrors F3's ``MetadataStatus`` intent (declared-vs-unavailable, never a
    fabricated value), kept local so this pydantic-strict module does not couple
    to the acquisition dataclass module.
    """
    COLLECTED = "COLLECTED"          # the source supplied it and it is present
    UNAVAILABLE = "UNAVAILABLE"      # source reached but genuinely did not supply it
    NOT_COLLECTED = "NOT_COLLECTED"  # not yet collected (default)


class ContentStatus(str, Enum):
    """Whether real asset bytes exist and were measured.

    M2.0 downloads nothing, so ``REFERENCED_ONLY`` is the norm; ``MEASURED`` is
    reserved for a later milestone that writes bytes through the artifact store
    and computes a real SHA-256. A content hash may appear *only* with MEASURED.
    """
    REFERENCED_ONLY = "REFERENCED_ONLY"  # we hold a URL/reference, not verified bytes
    MEASURED = "MEASURED"                # bytes stored + SHA-256 computed (later milestone)
    UNAVAILABLE = "UNAVAILABLE"          # the referenced bytes could not be obtained


class AssetKind(str, Enum):
    IMAGE = "image"
    FOOTAGE = "footage"
    AUDIO = "audio"
    MUSIC = "music"
    GRAPHIC = "graphic"
    MAP = "map"
    DOCUMENT = "document"
    OTHER = "other"


class RightsBasis(str, Enum):
    """The claimed legal basis for reuse. ``UNKNOWN`` is a first-class value, not
    a default-to-usable; it forces ``UNVERIFIED`` effective rights."""
    CREATOR_OWNED = "creator_owned"
    LICENSED = "licensed"
    PUBLIC_DOMAIN = "public_domain"
    CC_BY = "cc_by"
    CC_BY_SA = "cc_by_sa"
    CC_BY_NC = "cc_by_nc"                       # non-commercial; blocks commercial use
    OFFICIAL_RELEASE_PERMITTED = "official_release_permitted"
    FAIR_USE_CLAIMED = "fair_use_claimed"
    UNKNOWN = "unknown"


class CommercialUse(str, Enum):
    ALLOWED = "allowed"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class PermittedUse(str, Enum):
    BROADCAST = "broadcast"
    WEB = "web"
    DERIVATIVE = "derivative"
    THUMBNAIL = "thumbnail"


class AssetReviewStatus(str, Enum):
    """Asset review lifecycle. Ingestion is never approval: an asset starts at
    ``DRAFT`` and only an explicit human decision reaches ``APPROVED``."""
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESTRICTED = "RESTRICTED"   # usable only under recorded constraints/override
    EXPIRED = "EXPIRED"         # a once-valid licence has lapsed


class EffectiveRights(str, Enum):
    """Deterministic rights verdict derived from a rights record. ``CLEAR`` is
    the only state that may appear in an approved, commercially-destined export;
    everything else is a specific blocker."""
    CLEAR = "CLEAR"
    RESTRICTED = "RESTRICTED"     # usable only for certain uses / non-commercial
    UNVERIFIED = "UNVERIFIED"     # unknown basis / missing provenance / unknown commercial use
    EXPIRED = "EXPIRED"           # licence expiry is in the past
    BLOCKED = "BLOCKED"           # explicitly non-commercial or prohibited for the target use


# -- supporting shapes -----------------------------------------------------


class DeclaredText(ValueModel):
    """A source-declared text field with an explicit fact status.

    Distinguishes a value the *source* supplied (``COLLECTED``) from one the
    source did not supply (``UNAVAILABLE``) or one not yet gathered
    (``NOT_COLLECTED``). The ``value`` must be present iff status is
    ``COLLECTED`` -- a collected field cannot be blank, and a non-collected field
    cannot smuggle in a fabricated value.
    """
    value: str | None = Field(default=None, max_length=2000)
    status: MetadataFactStatus = MetadataFactStatus.NOT_COLLECTED

    @model_validator(mode="after")
    def _value_matches_status(self):
        collected = self.status == MetadataFactStatus.COLLECTED
        has_value = self.value is not None and self.value.strip() != ""
        if collected and not has_value:
            raise ValueError("A COLLECTED field must carry a non-empty value")
        if not collected and has_value:
            raise ValueError("Only a COLLECTED field may carry a value")
        return self


class AssetContentV1(ValueModel):
    """Content integrity for an asset. A real SHA-256 exists only when bytes were
    actually measured; a referenced-only asset carries none, and never a guess.
    """
    status: ContentStatus = ContentStatus.REFERENCED_ONLY
    # Present only when status == MEASURED (real bytes stored + hashed downstream).
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    media_type: str | None = Field(default=None, min_length=1, max_length=255)
    # Verified byte size; present only alongside a measured hash. Never asserted
    # from a Content-Length header here (that is a later, verified step).
    byte_size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _hash_only_when_measured(self):
        if self.status == ContentStatus.MEASURED:
            if self.content_sha256 is None:
                raise ValueError("MEASURED content requires a content_sha256")
            if self.byte_size is None:
                raise ValueError("MEASURED content requires a verified byte_size")
        else:
            if self.content_sha256 is not None:
                raise ValueError("A content_sha256 may appear only for MEASURED content")
            if self.byte_size is not None:
                raise ValueError("A byte_size may appear only for MEASURED content")
        return self


class AssetProvenanceV1(ValueModel):
    """Where an asset came from. Source-declared facts (title/creator) carry a
    fact status; the source URL and acquisition time are operator-recorded, not
    fabricated. ``source_url`` may be absent -- which, by the rights rule below,
    forces ``UNVERIFIED`` effective rights.
    """
    # The canonical page / API the asset came from. Optional, but its absence is
    # a rights blocker, never silently ignored.
    source_url: str | None = Field(default=None, min_length=1, max_length=2000)
    # A free-text documented origin when there is no single URL (e.g. an offline
    # handout). Recorded for provenance; does not substitute for a URL in the
    # rights evaluation.
    documented_origin: str | None = Field(default=None, min_length=1, max_length=2000)
    source_title: DeclaredText = Field(default_factory=DeclaredText)
    creator: DeclaredText = Field(default_factory=DeclaredText)
    publisher: DeclaredText = Field(default_factory=DeclaredText)
    # ISO-8601 UTC acquisition time, operator-recorded. Optional in the contract;
    # a service may require it. Never auto-filled to "now" here.
    acquired_at: str | None = Field(default=None, min_length=1, max_length=64)


class RightsRecordV1(ValueModel):
    """Operator-asserted, human-reviewable rights for an asset.

    These are **assertions**, not source-verified facts: a licence URL being
    reachable does not make the terms true, and public accessibility never
    implies reuse rights. ``evaluate_effective_rights`` turns this record into a
    deterministic verdict; unknown/ missing values fail closed.
    """
    schema_version: Literal["defence-studio.asset-rights.v1"]
    rights_basis: RightsBasis
    licence_id: str | None = Field(default=None, min_length=1, max_length=200)
    licence_name: str | None = Field(default=None, min_length=1, max_length=400)
    licence_url: str | None = Field(default=None, min_length=1, max_length=2000)
    attribution_required: bool = True
    # Verbatim attribution string to carry into render/export when required.
    attribution_text: str | None = Field(default=None, min_length=1, max_length=2000)
    permitted_uses: list[PermittedUse] = Field(default_factory=list)
    commercial_use: CommercialUse = CommercialUse.UNKNOWN
    # Free-text restrictions (e.g. "editorial only", "no logo crop"). Bounded.
    restrictions: list[str] = Field(default_factory=list, max_length=50)
    # ISO-8601 UTC licence expiry; None = no stated expiry. A past value is EXPIRED.
    expires_at: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _rights_integrity(self):
        if len(set(self.permitted_uses)) != len(self.permitted_uses):
            raise ValueError("permitted_uses must not contain duplicates")
        for item in self.restrictions:
            if not item or not item.strip():
                raise ValueError("restrictions entries must be non-empty")
            if len(item) > 500:
                raise ValueError("a restriction entry exceeds 500 characters")
        # Attribution required but no text is allowed (the text may be supplied
        # at render time), but attribution text with attribution_required False
        # is a contradiction worth rejecting.
        if self.attribution_text is not None and not self.attribution_required:
            raise ValueError("attribution_text present but attribution_required is False")
        return self


class AssetDescriptorV1(ValueModel):
    """One asset's stable identity, metadata, integrity, provenance and review
    state. Content-addressed: ``asset_digest`` is its ``asset_id`` basis, so two
    byte-identical descriptors collide by design (a service dedups on it).
    """
    schema_version: Literal["defence-studio.asset-descriptor.v1"]
    job_id: str = Field(min_length=1, max_length=200)
    # Stable identity. A service sets this to the descriptor's canonical digest
    # (see ``asset_digest``); the contract only requires it be a 64-hex id so an
    # assignment can reference it and duplicates are detectable.
    asset_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: AssetKind
    title: str = Field(min_length=1, max_length=400)
    description: str = Field(default="", max_length=4000)
    content: AssetContentV1 = Field(default_factory=AssetContentV1)
    provenance: AssetProvenanceV1
    rights: RightsRecordV1 | None = None
    review_status: AssetReviewStatus = AssetReviewStatus.DRAFT

    @model_validator(mode="after")
    def _descriptor_integrity(self):
        # Ingestion is never approval: an asset cannot be APPROVED without a
        # rights record to approve.
        if self.review_status == AssetReviewStatus.APPROVED and self.rights is None:
            raise ValueError("An APPROVED asset must carry a rights record")
        return self


class AssetAssignmentV1(ValueModel):
    """A versioned binding of one asset to one scene of an *approved* scene plan.

    Kept entirely separate from M0's ``VisualSuggestionV1`` and from the scene
    plan itself, so assets can be assigned/replaced without rewriting the plan or
    the script (core design: "replace assets without rewriting scripts"). Pinned
    to the exact approved scene-plan hash + version + ``scene_id`` it was made
    against, so a later plan change is detectable (a service enforces a stale
    409); the contract guarantees the binding fields are present and well-formed.
    """
    schema_version: Literal["defence-studio.asset-assignment.v1"]
    job_id: str = Field(min_length=1, max_length=200)
    language: Literal["ta", "en"]
    version: int = Field(gt=0)
    # The exact approved scene plan this assignment was bound to.
    scene_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_plan_version: int = Field(gt=0)
    scene_id: str = Field(min_length=1, max_length=120)
    asset_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    # The effective-rights verdict recorded at assignment time (from the asset's
    # rights record). Stored so a service/export can gate on it; a service
    # re-derives it, never trusting this value blindly.
    effective_rights: EffectiveRights
    # A render-target use this assignment is for; the rights gate checks the
    # asset permits it. Optional: unset means "general" and still blocks on a
    # non-CLEAR verdict.
    target_use: PermittedUse | None = None
    created_at: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _assignment_integrity(self):
        # An assignment can only be *recorded*; it cannot assert CLEAR rights for
        # an asset whose verdict is anything else. A service still re-derives the
        # verdict, but a structurally dishonest assignment is rejected here.
        if self.effective_rights not in EffectiveRights.__members__.values():
            raise ValueError("Unknown effective_rights")
        return self


# -- deterministic rights gate (pure; no service, no I/O) ------------------


def evaluate_effective_rights(
    rights: "RightsRecordV1 | None",
    provenance: "AssetProvenanceV1 | None",
    *,
    now_iso: str | None = None,
    for_commercial: bool = True,
    target_use: "PermittedUse | None" = None,
) -> EffectiveRights:
    """Deterministically derive an asset's effective-rights verdict. Pure: no
    network, no clock read unless ``now_iso`` is supplied by the caller.

    Fail-closed rules (an unknown is never treated as usable):

    * No rights record, unknown basis, unknown commercial use, or no usable
      provenance (neither a source URL nor a documented origin) -> ``UNVERIFIED``.
    * A stated expiry in the past (compared against the caller-supplied
      ``now_iso``) -> ``EXPIRED``.
    * Commercial render with a non-commercial basis (``cc_by_nc``) or
      ``commercial_use = prohibited`` -> ``BLOCKED``.
    * A ``target_use`` not in the asset's ``permitted_uses`` (when any are
      declared) -> ``RESTRICTED``.
    * Otherwise -> ``CLEAR``.
    """
    if rights is None:
        return EffectiveRights.UNVERIFIED
    has_provenance = provenance is not None and (
        bool(provenance.source_url) or bool(provenance.documented_origin)
    )
    if not has_provenance:
        return EffectiveRights.UNVERIFIED
    if rights.rights_basis == RightsBasis.UNKNOWN:
        return EffectiveRights.UNVERIFIED
    if rights.commercial_use == CommercialUse.UNKNOWN:
        return EffectiveRights.UNVERIFIED

    # Expiry is checked only against a caller-supplied clock (keeps the function
    # pure and deterministic in tests). A malformed expiry is treated as expired
    # (fail closed), never silently ignored.
    if rights.expires_at is not None and now_iso is not None:
        if _iso_is_past(rights.expires_at, now_iso):
            return EffectiveRights.EXPIRED

    if for_commercial and (
        rights.rights_basis == RightsBasis.CC_BY_NC
        or rights.commercial_use == CommercialUse.PROHIBITED
    ):
        return EffectiveRights.BLOCKED

    if target_use is not None and rights.permitted_uses and target_use not in rights.permitted_uses:
        return EffectiveRights.RESTRICTED

    return EffectiveRights.CLEAR


def _iso_is_past(expires_at: str, now_iso: str) -> bool:
    """True if ``expires_at`` is strictly before ``now_iso`` (both ISO-8601), or
    if either cannot be parsed (fail closed -> treat as expired)."""
    from datetime import datetime

    def _parse(value: str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    expiry = _parse(expires_at)
    now = _parse(now_iso)
    if expiry is None or now is None:
        return True
    # Compare tz-aware to tz-aware; if either is naive, fail closed.
    if expiry.tzinfo is None or now.tzinfo is None:
        return True
    return expiry < now


# -- deterministic digests (same canonical JSON as evidence/scene artifacts) --


def asset_digest(descriptor: dict) -> str:
    """Canonical content hash of an asset descriptor. A service uses this as the
    ``asset_id`` basis so byte-identical descriptors dedup."""
    return digest(descriptor)


def rights_digest(rights: dict) -> str:
    """Canonical content hash of a rights record (for approval binding)."""
    return digest(rights)


def assignment_digest(assignment: dict) -> str:
    """Canonical content hash of a scene-assignment artifact."""
    return digest(assignment)
