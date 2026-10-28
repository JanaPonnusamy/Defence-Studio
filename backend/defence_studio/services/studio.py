"""Unified studio read model: one ordered stage list for a job/language.

Pure mapping over ``pipeline_overview`` + script ``revision_staleness``; it
owns no state, so the studio view is identical after a refresh, a restart or
on another device. Stage order follows what the pipeline actually enforces
(the scene plan is approved before audio is generated per scene).

Raw stage statuses are mapped onto one studio vocabulary; the raw value is
always returned alongside. A stage without a human review gate is reported
with ``review_gate: False`` -- generation there is never presented as an
approval.
"""
from __future__ import annotations

STUDIO_SCHEMA = "defence-studio.studio-state.v1"

STUDIO_STATES = ("NOT_STARTED", "DRAFT", "QUEUED", "RUNNING", "GENERATED", "AWAITING_REVIEW",
                 "APPROVED", "REJECTED", "FAILED", "CANCELLED", "STALE", "COMPLETED")

_RAW_TO_STUDIO = {
    "NONE": "NOT_STARTED", "PRESENT": "GENERATED", "SUCCEEDED": "GENERATED",
    "CHANGES_REQUESTED": "REJECTED", "VALIDATION_FAILED": "FAILED", "INTERRUPTED": "FAILED",
    "SUPERSEDED": "STALE",
}

# key, title, overview stage, UI tab that owns the stage's actions, human review gate
STAGES = (
    ("research", "Topic & research", "evidence_package", "research", True),
    ("script", "Script", "script", "script", True),
    ("scene_plan", "Scene plan", "scene_plan", "scene", True),
    ("audio", "Voice & audio", "audio_set", "production", False),
    ("visuals", "Scene visuals", "visual_timeline", "production", False),
    ("render", "Render", "render", "production", False),
    ("qc", "Quality check", "qc_report", "production", False),
    ("final_review", "Final review", "final_approval", "production", True),
    ("export", "Export package", "publish_package", "production", True),
)


def studio_state_name(raw: str | None, stale: bool = False) -> str:
    raw = raw or "NONE"
    if raw == "NONE":
        return "NOT_STARTED"
    if stale:
        return "STALE"
    return _RAW_TO_STUDIO.get(raw, raw)  # unknown values pass through rather than being guessed


def build_studio_state(overview: dict, script_staleness: dict | None) -> dict:
    by_stage = {s["stage"]: s for s in overview["stages"]}
    stale_scripts = {r["version"]: r for r in (script_staleness or {}).get("revisions", [])
                     if r.get("language") == overview["language"]}
    stages, current = [], None
    for key, title, source, tab, gate in STAGES:
        src = by_stage.get(source, {"status": "NONE"})
        raw = src.get("status", "NONE")
        stale, reason = bool(src.get("stale")), "Inputs changed after this was produced" if src.get("stale") else ""
        approved = src.get("approved_version")
        if key == "script" and approved is not None:
            check = stale_scripts.get(approved)
            if check and check.get("state") == "STALE":
                stale, reason = True, check.get("reason", "")
        state = studio_state_name(raw, stale)
        if key == "script":
            # A newer draft does not undo an approved, still-current version.
            done = approved is not None and not stale
        else:
            done = state in ("APPROVED", "COMPLETED") or (not gate and state == "GENERATED")
        entry = {"key": key, "title": title, "tab": tab, "state": state, "raw_status": raw,
                 "version": src.get("version"), "approved_version": approved, "review_gate": gate,
                 "done": done, "stale_reason": reason, "detail": src.get("detail", "")}
        if current is None and not done:
            current = key
        stages.append(entry)
    publishing = by_stage.get("publishing", {"status": "NONE"})
    return {"schema": STUDIO_SCHEMA, "job_id": overview["job_id"], "language": overview["language"],
            "current_stage": current, "stages": stages,
            "workflow": overview.get("workflow"), "needs_attention": overview.get("needs_attention", []),
            # Publishing is a separate, explicitly authorised action; shown, never driven from here.
            "publishing": {"state": publishing.get("status", "NONE"), "separate_authorised_action": True},
            "generated_at": overview.get("generated_at")}
