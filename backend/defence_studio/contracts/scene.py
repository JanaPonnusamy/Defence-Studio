"""Phase 4 scene-plan contract (M0 — contract foundation only).

This module defines the *stable, versioned* scene-plan shape that the future
Phase 4 scene service and the downstream TTS / alignment / render adapters will
implement independently. It is deliberately a contract only: it runs no network
call, touches no database, imports nothing from TTS / alignment / FFmpeg, and
generates no scenes. It exists so later milestones can be built against a fixed
schema without coupling to one another.

Two boundaries live here, mirroring ``contracts/script.py``:

1. The *request* boundary -- :class:`ScenePlanRequestV1` -- the minimal input
   that pins a scene plan to the exact approved Phase 3 script revision (and its
   approved evidence package) it is derived from, plus bounded planning
   configuration. It never carries narration or generated scenes; it is the
   instruction to build a plan, hash-bound to its upstream approvals.

2. The *persisted artifact* boundary -- :class:`ScenePlanV1` -- the versioned,
   editable, re-orderable plan the future scene service will store, review, and
   approve. It carries ordered scenes, each referencing (not rewriting) a slice
   of the approved narration and the approved evidence claim IDs that slice
   rests on, presentation metadata (visual suggestions, voice direction,
   captions, transitions, crop, music cues) kept strictly *out* of spoken text,
   and an honest planned-vs-measured timing distinction.

Honesty rules this contract enforces structurally:

* **Narration is referenced, never silently rewritten.** A scene copies the
  approved spoken text verbatim and names the ``section_id`` it came from. The
  contract cannot by itself prove a copy is faithful, but it records the source
  section so a future service/validator can check it.
* **Measured word/scene timing does not exist until real alignment has run.**
  ``planned_timing`` is always present (an estimate); ``measured_timing`` is
  ``None`` until an alignment artifact is referenced, and the model rejects a
  measured timing that claims no alignment source.
* **Downstream references are slots, not internals.** Asset / TTS / alignment /
  render links are optional hash-identified references to artifacts produced by
  other components; no implementation-specific payload is embedded here.
* **Claim IDs must stay within the approved evidence.** Every claim a scene
  cites must be in the plan's ``eligible_claim_ids`` snapshot (itself taken from
  the approved package), so a scene can never reference a claim the approved
  evidence does not contain.

Approval, re-ordering, per-scene editing, and generation are **not** implemented
in M0; the ``status`` vocabulary and the timing/reference slots are defined now
so those later milestones have a fixed target.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from defence_studio.contracts.evidence import StrictModel, digest

# -- identities ------------------------------------------------------------

SCENE_PLAN_SCHEMA = "defence-studio.scene-plan.v1"
SCENE_PLAN_REQUEST_SCHEMA = "defence-studio.scene-plan-request.v1"
# Bumped independently of the script/evidence schema versions so a future scene
# generator is identified distinctly from the script generator that feeds it.
SCENE_PLANNER_VERSION = "defence-studio.scene-plan.v1"

LANGUAGES = ("ta", "en")

# How a scene's planned (not measured) duration is derived. Mirrors the script
# contract's ``words-per-minute.v1`` method name so the two estimates are
# reconcilable; it is always an estimate, never an exact recording time.
PLANNED_TIMING_METHOD = "words-per-minute.v1"

# A scene plan moves through these explicit, auditable states. Approval is never
# implied by generation or by editing; it is bound to a plan hash by a future
# milestone (M1), the same stale-revision pattern Phase 2/3 already use.
SCENE_PLAN_STATUSES = (
    "DRAFT",            # generated or hand-built, freely editable
    "AWAITING_REVIEW",  # submitted for human review
    "CHANGES_REQUESTED",
    "APPROVED",         # explicitly approved, bound to this plan's hash and its script revision
    "SUPERSEDED",       # a newer approval or a changed upstream script invalidated it
)

# A transition hint between consecutive scenes. Presentation metadata only.
SCENE_TRANSITIONS = ("cut", "fade", "dissolve", "slide", "none")


# -- request boundary ------------------------------------------------------


class ScenePlanConfigV1(StrictModel):
    """Bounded, caller-supplied planning settings. No secret ever lives here.

    These shape how an initial plan is generated (e.g. one scene per narration
    section as a default), not what any downstream component does with it.
    """
    language: Literal["ta", "en"]
    target_total_seconds: int = Field(ge=30, le=3600)
    words_per_minute: int = Field(default=150, gt=0, le=400)
    # A default pacing hint for generated scenes; individual scenes may override.
    default_transition: Literal["cut", "fade", "dissolve", "slide", "none"] = "cut"


class ScenePlanRequestV1(StrictModel):
    """Instruction to build a scene plan, pinned to its upstream approvals.

    ``script_revision_sha256`` binds to the exact approved Phase 3 script
    revision this plan is derived from; ``package_sha256`` is that revision's
    approved evidence package. Both are recorded so a future service can reject
    a plan built on a now-superseded script/package (the same 409 stale-revision
    pattern Phase 2/3 enforce) rather than silently drifting.
    """
    schema_version: Literal["defence-studio.scene-plan-request.v1"]
    job_id: str = Field(min_length=1)
    language: Literal["ta", "en"]
    script_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    script_version: int = Field(gt=0)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: ScenePlanConfigV1

    @model_validator(mode="after")
    def _language_matches_config(self):
        if self.config.language != self.language:
            raise ValueError("Config language must match the request language")
        return self


# -- persisted artifact: supporting shapes ---------------------------------


class VoiceDirectionV1(StrictModel):
    """Per-scene narration delivery guidance — metadata only, never spoken.

    These are hints for a future TTS adapter. They are kept in their own object,
    strictly separate from ``narration`` text, so direction tokens can never be
    read aloud. No value here changes the approved words; it only guides
    delivery, and a downstream adapter is free to ignore unsupported fields.
    """
    tone: str = Field(default="measured", min_length=1, max_length=200)
    # Relative rate/pitch multipliers around a neutral 1.0; bounded, not absolute
    # engine parameters, so they stay provider-independent.
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)
    # A requested pause in milliseconds after this scene's narration. Planning
    # metadata; the real inter-scene gap is only known after alignment/render.
    pause_after_ms: int = Field(default=0, ge=0, le=10000)


class CaptionCueV1(StrictModel):
    """A caption instruction for a scene. Timing stays planned until alignment.

    ``text`` is normally the scene's own narration (captions mirror speech); it
    is stored explicitly so a future render can lay captions without re-deriving
    them. Real on-screen timing comes from alignment, never from this field.
    """
    text: str = Field(min_length=1, max_length=20000)
    style: str = Field(default="default", min_length=1, max_length=120)


class VisualSuggestionV1(StrictModel):
    """A non-binding visual idea for a scene. Not an asset assignment.

    M0 deliberately carries only a human-readable suggestion and an optional
    neutral kind. Actual asset selection, rights review, and attribution are
    Phase 5 (a separate contract) — nothing here fabricates an asset, a URL, or
    a rights basis.
    """
    description: str = Field(min_length=1, max_length=2000)
    kind: Literal["footage", "image", "graphic", "map", "text_card", "unspecified"] = "unspecified"


class CropHintV1(StrictModel):
    """An optional framing hint (e.g. target aspect ratio) for a scene's visual.

    Expressed as a provider-independent aspect ratio plus an optional focus
    point in normalized 0..1 coordinates, so it carries no renderer internals.
    """
    aspect_ratio: str = Field(pattern=r"^\d{1,2}:\d{1,2}$")
    focus_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focus_y: float = Field(default=0.5, ge=0.0, le=1.0)


class MusicCueV1(StrictModel):
    """An optional background-music intent for a scene. Metadata only.

    No audio file is referenced in M0 (that is a later asset/rights concern);
    this records intent and a bounded relative gain so planning stays honest.
    """
    intent: Literal["none", "bed", "sting", "swell"] = "none"
    gain_db: float = Field(default=-18.0, ge=-60.0, le=0.0)


class PlannedTimingV1(StrictModel):
    """A deterministic *estimate* of a scene's duration. Never a measured time.

    Derived from the scene's spoken word count and a words-per-minute rate, the
    same method the script contract uses. ``is_estimate`` is pinned ``True`` so
    this value can never be mistaken for a real recording/alignment duration.
    """
    words: int = Field(ge=0)
    words_per_minute: int = Field(gt=0, le=400)
    estimated_seconds: float = Field(ge=0.0)
    method: Literal["words-per-minute.v1"]
    is_estimate: Literal[True] = True


class MeasuredTimingV1(StrictModel):
    """A scene's *measured* start/duration, sourced from real alignment output.

    This object only ever exists once an alignment artifact has produced it;
    ``alignment_sha256`` names that artifact. ``is_synthetic`` propagates the
    upstream alignment engine's own honesty flag unchanged — a measured timing
    produced by a deterministic/test engine must carry ``is_synthetic: true`` so
    a render can never present it as genuine alignment (a hard honesty gate the
    Phase 4 plan calls for).
    """
    start_seconds: float = Field(ge=0.0)
    duration_seconds: float = Field(gt=0.0)
    alignment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_synthetic: bool


class ArtifactRefV1(StrictModel):
    """A hash-identified reference to an artifact produced by another component.

    Used for the optional downstream slots (generated audio, alignment output,
    render output). It carries only a type, a URI, a content hash, and the
    producing artifact's own schema version — never that component's internal
    payload — so the scene plan stays decoupled from TTS/alignment/render
    internals while still being able to bind to their outputs by reference and
    re-verify them (the "never trust, re-verify" pattern F4 already applies).
    """
    artifact_type: str = Field(min_length=1, max_length=120)
    uri: str = Field(min_length=1, max_length=2000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(min_length=1, max_length=120)


class SceneV1(StrictModel):
    """One ordered scene: a referenced narration slice plus presentation metadata.

    The spoken ``narration`` is a verbatim slice of the approved script's
    section (identified by ``source_section_id``); it is never rewritten here.
    ``claim_ids`` are the approved-evidence claims that slice rests on and must
    all be within the plan's eligible set. Every other field is presentation or
    delivery metadata kept out of the spoken text, or an honest timing/reference
    slot. Downstream reference slots default to ``None`` because in M0 no TTS,
    alignment, or render has run — they are populated by later milestones.
    """
    scene_id: str = Field(min_length=1, max_length=120)
    # 1-based position; must equal the scene's index+1 in order (validated on the plan).
    order: int = Field(ge=1)
    # The approved script section this scene's narration was sliced from.
    source_section_id: str = Field(min_length=1, max_length=120)
    # Verbatim spoken text for this scene. Free of citation IDs and metadata.
    narration: str = Field(min_length=1, max_length=20000)
    # Approved-evidence claims this narration slice rests on (subset of the plan's
    # eligible_claim_ids). May be empty for a non-material scene (e.g. a hook).
    claim_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(default="", max_length=400)

    # Presentation / delivery metadata — never spoken.
    voice_direction: VoiceDirectionV1 = Field(default_factory=VoiceDirectionV1)
    visual_suggestions: list[VisualSuggestionV1] = Field(default_factory=list)
    captions: list[CaptionCueV1] = Field(default_factory=list)
    transition_in: Literal["cut", "fade", "dissolve", "slide", "none"] = "cut"
    crop: CropHintV1 | None = None
    music: MusicCueV1 | None = None

    # Timing: planned is always present; measured appears only after alignment.
    planned_timing: PlannedTimingV1
    measured_timing: MeasuredTimingV1 | None = None

    # Optional downstream artifact slots (populated by later milestones, not M0).
    audio_ref: ArtifactRefV1 | None = None
    alignment_ref: ArtifactRefV1 | None = None
    render_ref: ArtifactRefV1 | None = None

    @model_validator(mode="after")
    def _scene_integrity(self):
        if self.narration.strip() == "":
            raise ValueError("Scene narration must contain spoken text")
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("Scene claim_ids must not contain duplicates")
        # A measured timing cannot exist without naming the alignment artifact
        # that produced it; and if it does exist the plan must also carry the
        # alignment reference (cross-checked on the plan). Here we only guard the
        # local invariant: measured timing implies an alignment hash is present
        # (already required by MeasuredTimingV1) and matches alignment_ref when set.
        if self.measured_timing is not None and self.alignment_ref is not None:
            if self.measured_timing.alignment_sha256 != self.alignment_ref.sha256:
                raise ValueError("measured_timing.alignment_sha256 must match alignment_ref.sha256")
        return self


# -- persisted artifact: the scene plan ------------------------------------


class ScenePlanV1(StrictModel):
    """The versioned, editable, re-orderable scene plan for one job+language.

    Bound by ``script_revision_sha256`` / ``package_sha256`` to the exact
    approved Phase 3 script and evidence it was derived from. ``scenes`` are
    strictly ordered and uniquely identified; ``eligible_claim_ids`` is the
    snapshot of approvable claims taken from the approved package at plan-build
    time, retained so scene-level claim references can be validated reproducibly
    without re-reading the package. Downstream (TTS/alignment/render) references
    live only as optional per-scene slots, so this plan unblocks later
    milestones without embedding any of their internals.
    """
    schema_version: Literal["defence-studio.scene-plan.v1"]
    job_id: str = Field(min_length=1)
    language: Literal["ta", "en"]
    version: int = Field(gt=0)
    planner_version: Literal["defence-studio.scene-plan.v1"]
    # Upstream binding (stable identity): the approved script revision + its package.
    script_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    script_version: int = Field(gt=0)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenes: list[SceneV1] = Field(min_length=1, max_length=500)
    # Snapshot of the approved package's eligible claims at plan-build time.
    eligible_claim_ids: list[str] = Field(default_factory=list)
    status: Literal["DRAFT", "AWAITING_REVIEW", "CHANGES_REQUESTED", "APPROVED", "SUPERSEDED"]
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def _plan_integrity(self):
        if self.planner_version != SCENE_PLANNER_VERSION:
            raise ValueError("Unexpected planner_version")
        seen: set[str] = set()
        allowed = set(self.eligible_claim_ids)
        for index, scene in enumerate(self.scenes):
            if scene.scene_id in seen:
                raise ValueError(f"Duplicate scene_id: {scene.scene_id}")
            seen.add(scene.scene_id)
            if scene.order != index + 1:
                raise ValueError(
                    f"scenes must be ordered: scene {scene.scene_id} has order "
                    f"{scene.order} at position {index + 1}"
                )
            if not set(scene.claim_ids) <= allowed:
                raise ValueError(
                    f"Scene {scene.scene_id} cites claim_ids outside the approved "
                    "evidence (eligible_claim_ids)"
                )
            # A plan that claims measured timing for a scene must also carry that
            # scene's alignment reference — measured timing never appears without
            # a referenced alignment artifact to re-verify it against.
            if scene.measured_timing is not None and scene.alignment_ref is None:
                raise ValueError(
                    f"Scene {scene.scene_id} has measured_timing but no alignment_ref; "
                    "measured timing requires a referenced alignment artifact"
                )
        if self.language not in LANGUAGES:
            raise ValueError("Unsupported language")
        return self


def validate_request(value: dict) -> dict:
    """Validate a scene-plan request and return its canonical dict."""
    return ScenePlanRequestV1.model_validate(value).model_dump()


def validate_scene_plan(value: dict) -> dict:
    """Validate a persisted scene-plan artifact and return its canonical dict."""
    return ScenePlanV1.model_validate(value).model_dump()


# -- deterministic planned-timing helper -----------------------------------


def count_words(text: str) -> int:
    """Count whitespace-delimited tokens (same rule as the script contract).

    Tamil and English narration are both written with spaces between words, so a
    whitespace split is a deterministic word count for both. Feeds a planned
    duration *estimate* only — never a measured time.
    """
    return len((text or "").split())


def plan_scene_timing(narration: str, words_per_minute: int = 150) -> dict:
    """Deterministic planned-timing estimate for one scene's narration.

    Explicitly an estimate (``is_estimate: true``); the real per-scene duration
    is only known once alignment has run and produced a ``MeasuredTimingV1``.
    """
    if words_per_minute <= 0:
        raise ValueError("words_per_minute must be positive")
    words = count_words(narration)
    seconds = words / words_per_minute * 60
    return {
        "words": words,
        "words_per_minute": words_per_minute,
        "estimated_seconds": float(seconds),
        "method": PLANNED_TIMING_METHOD,
        "is_estimate": True,
    }


def plan_digest(plan: dict) -> str:
    """Deterministic content hash of a scene plan (canonical JSON, same as
    evidence/script artifacts). Two byte-identical plans hash identically; a
    future service binds approval to this digest."""
    return digest(plan)
