"""
Shot Planner (spec §3, §9-§12, §15).

Narration-driven planning:
  1. every narration segment (sentence) is classified (intent, entities, numbers)
  2. consecutive segments are grouped into BEATS; a new beat (= a visual
     change) starts when the subject/intent changes, a new entity appears, a
     statistic or claim is introduced, evidence/documents or a map are needed,
     emphasis changes (escalation/conclusion) or a major sentence begins
     (spec §12). Short connective sentences stay on the current visual.
  3. each beat gets an asset from the selector (or an explicit GAP)
  4. optional AI pass: at most `max_ai_images` (default 4, spec §10) hook /
     illustrative images are generated through the provider-neutral adapter;
     other illustrative beats reuse them with new framing
  5. beats longer than `max_shot_seconds` are split into several shots of the
     SAME asset, each with a different framing from the asset's ladder
     (spec §11) -- split points snap to sentence boundaries when possible.
     The fixed duration is only this fallback, never the primary cut logic.
"""
from __future__ import annotations

import math
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Asset, Scene, Shot
from app.errors import VideoModuleError
from app.pipeline.intent import INTENT_VISUALS, classify_segment
from app.pipeline.motion import compute_crop, ladder_for, plan_motion
from app.pipeline.selector import AssetIndex, SelectionResult, select_asset

MAX_SAME_ASSET_BEATS = 3
_CUE = {
    "STATISTIC": "a statistic appears",
    "EVIDENCE": "evidence / a document is referenced",
    "GEOGRAPHIC_CONTEXT": "a map / geographic context is needed",
    "LOCATION": "location changes",
    "COMPARISON": "a comparison is introduced",
    "TIMELINE": "timeline context begins",
    "ESCALATION": "emotional emphasis changes (escalation)",
    "CONCLUSION": "conclusion begins",
    "HARDWARE_DETAIL": "hardware detail is discussed",
    "MILITARY_CAPABILITY": "military capability is discussed",
    "ENTITY_INTRODUCTION": "a new entity is introduced",
    "EVENT_EXPLANATION": "subject changes",
}


@dataclass(eq=False)
class Beat:
    scene: Scene
    scene_first: bool
    segments: list[dict]
    info: dict
    change_reason: str
    continuation: bool = False
    start: float = 0.0
    end: float = 0.0
    selection: SelectionResult | None = None
    entities: list[str] = field(default_factory=list)

    def merged_info(self) -> dict:
        tokens, ents, nums = set(), [], []
        for s in self.segments:
            c = s["_info"]
            tokens |= set(c["tokens"])
            ents += [e for e in c["entities"] if e not in ents]
            nums += c["numbers"]
        return {**self.info, "tokens": sorted(tokens), "entities": ents, "numbers": nums}


@dataclass
class PlanOptions:
    generate_ai: bool = False
    ai_provider: str = "placeholder-procedural"
    max_ai_images: int = 4
    ai_hook: bool = True
    ai_quality: str = "standard"
    ai_resolution: str = "1280x720"
    allow_unknown_rights: bool = False
    max_shot_seconds: float | None = None
    min_shot_seconds: float | None = None
    # Phase 2
    allow_placeholder_fallback: bool | None = None   # explicit opt-in for labelled placeholder fallback
    output_width: int | None = None                  # renderer output frame (optional; recorded in the timeline)
    output_height: int | None = None
    frame_rate: float | None = None
    # Opt-in: a statistic/timeline/comparison beat with no sourced graphic may show a
    # strictly relevant rights-cleared photo instead of a GAP (never AI, never a fake chart).
    photo_fallback_for_data_intents: bool = False


DATA_INTENTS = ("STATISTIC", "TIMELINE", "COMPARISON")
PHOTO_TYPES = ["SOURCE_PHOTO", "IMAGE"]


@dataclass
class PlanOutput:
    shots: list[Shot]
    stats: dict
    warnings: list[str]
    generated_asset_ids: list[str]
    assets: dict[str, Asset]


GenerateFn = Callable[[str, str, str | None], Asset]  # (prompt, purpose, scene_id) -> Asset


def build_beats(scene: Scene, is_first_scene: bool) -> list[Beat]:
    segs = scene.narration_segments or []
    if not segs:
        raise VideoModuleError("MISSING_NARRATION_ANCHOR", f"Scene {scene.scene_id} has no narration segments.")
    beats: list[Beat] = []
    seen_entities: set[str] = set()
    for seg in segs:
        seg = dict(seg)
        info = classify_segment(seg["text"])
        seg["_info"] = info
        words = len(seg["text"].split())
        cur = beats[-1] if beats else None
        reason, continuation = None, False
        if cur is None:
            reason = "opening hook / scene begins" if is_first_scene else "new scene begins"
        elif info["weak"] and words < 8 and not info["entities"]:
            reason = None  # short connective sentence: stay on the current visual
        elif info["primary_intent"] != cur.info["primary_intent"]:
            reason = _CUE[info["primary_intent"]]
        else:
            new_ents = [e for e in info["entities"] if e not in seen_entities]
            if new_ents:
                reason = f"new entity appears: {new_ents[0]}"
            elif info["numbers"]:
                reason = "a new claim / figure is introduced"
            elif words >= 8:
                reason, continuation = "a major sentence / idea begins", True
        seen_entities |= set(info["entities"])
        if reason is None:
            cur.segments.append(seg)
        else:
            beats.append(Beat(scene=scene, scene_first=cur is None, segments=[seg], info=info,
                              change_reason=reason, continuation=continuation))
    for i, b in enumerate(beats):
        b.start = scene.start_time if i == 0 else float(b.segments[0]["start"])
        b.end = beats[i + 1].segments[0]["start"] if i + 1 < len(beats) else scene.end_time
        b.end = max(b.end, b.start + 0.05)
    return beats


def _select_with_fallback(index: AssetIndex, info: dict, allow_unknown: bool, avoid: set[str],
                          photo_fallback: bool = False) -> SelectionResult:
    """Try the primary intent, then the sentence's secondary intents (spec §14 fallback, e.g. a
    claim 'according to monitors' about a location falls back from DOCUMENT to MAP). The beat-level
    `ai_allowed` flag is kept, so a fallback can never open the door to AI imagery for evidence."""
    sel = _select_by_intents(index, info, allow_unknown, avoid)
    primary = info["primary_intent"]
    if sel.asset is None and photo_fallback and primary in DATA_INTENTS:
        # Keeps the data intent's strict-relevance rule: only a photo matching this narration qualifies.
        photo = select_asset(index, {**info, "preferred_visuals": PHOTO_TYPES},
                             allow_unknown_rights=allow_unknown, avoid_asset_ids=avoid)
        if photo.asset is not None and photo.relevance > 0:
            photo.editorial_reason = (f"No sourced {primary.lower()} graphic exists; showing a relevant "
                                      "rights-cleared photo while the figure is narrated (owner option "
                                      "photo_fallback_for_data_intents). The photo illustrates, it is not the data.")
            return photo
    return sel


def _select_by_intents(index: AssetIndex, info: dict, allow_unknown: bool, avoid: set[str]) -> SelectionResult:
    first: SelectionResult | None = None
    primary = info["primary_intent"]
    for intent in info["intents"][:3]:
        sub = {**info, "primary_intent": intent, "preferred_visuals": INTENT_VISUALS[intent][0]}
        sel = select_asset(index, sub, allow_unknown_rights=allow_unknown, avoid_asset_ids=avoid)
        if sel.asset is not None and intent != primary and sel.relevance <= 0:
            sel = SelectionResult(None, 0, 0.0, False, None, "")  # fallbacks must be specifically relevant
        if sel.asset is not None:
            if intent != primary:
                sel.editorial_reason = (f"{sel.editorial_reason[:-1]}; fallback from {primary} "
                                        f"(no suitable source) to secondary intent {intent}.")
            return sel
        if first is None:
            first = sel
    return first


def _ai_prompt(beat: Beat) -> str:
    text = " ".join(s["text"] for s in beat.segments)[:240]
    intent = beat.info["primary_intent"].replace("_", " ").lower()
    return f"Cinematic illustrative visual ({intent}), non-photographic, not evidence: {text}"


def _split_points(beat: Beat, n: int, min_shot: float) -> list[float]:
    d = beat.end - beat.start
    if n <= 1:
        return [beat.start, beat.end]
    seg_starts = [float(s["start"]) for s in beat.segments[1:]]
    pts = [beat.start]
    for k in range(1, n):
        ideal = beat.start + k * d / n
        cand = min(seg_starts, key=lambda t: abs(t - ideal)) if seg_starts else None
        p = cand if cand is not None and abs(cand - ideal) < 0.4 * d / n else ideal
        if p - pts[-1] < min_shot or beat.end - p < min_shot:
            p = ideal
        pts.append(p)
    pts.append(beat.end)
    return pts


def _anchor(beat: Beat, start: float, end: float) -> dict:
    segs = [s for s in beat.segments if float(s["start"]) < end - 1e-6 and float(s["end"]) > start + 1e-6] or beat.segments[:1]
    return {
        "segment_ids": [s["segment_id"] for s in segs],
        "text": " ".join(s["text"] for s in segs)[:600],
        "start": float(segs[0]["start"]),
        "end": float(segs[-1]["end"]),
        "timing_source": beat.scene.timing_source,
    }


def plan_project(
    db: Session, project_id: str, scenes: list[Scene], options: PlanOptions, *,
    generate: GenerateFn | None = None, progress: Callable[[float, str], None] | None = None,
) -> PlanOutput:
    settings = get_settings()
    max_shot = options.max_shot_seconds or settings.max_shot_seconds
    min_shot = options.min_shot_seconds or settings.min_shot_seconds
    if min_shot <= 0 or max_shot < min_shot:
        raise VideoModuleError("VALIDATION_ERROR", "Need 0 < min_shot_seconds <= max_shot_seconds.")
    warnings: list[str] = []
    assets = db.query(Asset).filter(Asset.project_id == project_id).all()
    index = AssetIndex.build(assets)
    by_id = {a.asset_id: a for a in assets}

    # ---- 1-2: beats
    beats: list[Beat] = []
    for si, scene in enumerate(scenes):
        beats.extend(build_beats(scene, si == 0))
    if progress:
        progress(0.2, f"parsed {len(beats)} narration beats")

    # ---- 3: selection
    prev_asset_id, run = None, 0
    for bi, b in enumerate(beats):
        info = b.merged_info()
        prev_sel = beats[bi - 1].selection if bi > 0 else None
        if (b.continuation and prev_sel and prev_sel.asset is not None and run < MAX_SAME_ASSET_BEATS
                and not b.scene_first):
            b.selection = SelectionResult(prev_sel.asset, prev_sel.tier, prev_sel.relevance, False, None,
                                          "Continuation of the same subject: same asset, new framing (spec §11).")
        else:
            b.selection = _select_with_fallback(index, info, options.allow_unknown_rights,
                                                {prev_asset_id} if prev_asset_id else set(),
                                                options.photo_fallback_for_data_intents)
        aid = b.selection.asset.asset_id if b.selection.asset else None
        run = run + 1 if aid and aid == prev_asset_id else 1
        prev_asset_id = aid
        if aid:
            index.usage[aid] += 1
    if progress:
        progress(0.4, "assets selected")

    # ---- 4: optional bounded AI generation (hook + illustrative gaps)
    generated: list[str] = []
    if options.generate_ai and generate is not None:
        cap = max(0, min(options.max_ai_images, settings.hard_max_ai_images_per_plan))
        targets: list[tuple[Beat, str]] = []
        first_scene_id = beats[0].scene.scene_id if beats else None
        if options.ai_hook:
            # hook/hero: first beat of the opening scene where an illustration is editorially allowed
            hook = next((b for b in beats if b.scene.scene_id == first_scene_id and b.info["ai_allowed"]), None)
            if hook is not None:
                targets.append((hook, "HOOK"))
        scenes_done = {first_scene_id} if targets else set()
        taken = {id(t[0]) for t in targets}
        # then: illustrative beats with no source at all, then beats that only got a non-specific
        # (relevance 0) source picture -- one per scene, HIGH-importance scenes first
        needing = [b for b in beats if id(b) not in taken and b.info["ai_allowed"] and b.selection
                   and (b.selection.needs_generation or (b.selection.tier == 1 and b.selection.relevance <= 0))]
        needing.sort(key=lambda b: (not b.selection.needs_generation, b.scene.importance != "HIGH",
                                    b.scene.order_index))
        for b in needing:
            if b.scene.scene_id not in scenes_done:
                targets.append((b, "CONCLUSION" if b.info["primary_intent"] == "CONCLUSION" else "ILLUSTRATION"))
                scenes_done.add(b.scene.scene_id)
        for b, purpose in targets[:cap]:
            try:
                asset = generate(_ai_prompt(b), purpose, b.scene.scene_id)
            except VideoModuleError as exc:
                warnings.append(f"AI generation for scene '{b.scene.title}' failed: {exc.code} {exc.message}")
                if exc.code in ("BUDGET_EXCEEDED", "PROVIDER_QUOTA", "PROVIDER_NOT_CONFIGURED", "PROVIDER_AUTH_FAILED"):
                    break
                continue
            generated.append(asset.asset_id)
            index.add(asset)
            by_id[asset.asset_id] = asset
            label = "HOOK/hero" if purpose == "HOOK" else "illustrative"
            if asset.generation_kind == "PLACEHOLDER":
                fb = f" FALLBACK after {asset.fallback_from} failed;" if asset.is_fallback else ""
                b.selection = SelectionResult(asset, 3, 1.0, False, None,
                                              f"PLACEHOLDER {label} image via adapter ({fb} procedural, not AI, not "
                                              "evidence; replace before publication).")
            else:
                mock = " MOCK/test-fixture" if asset.is_mock else ""
                b.selection = SelectionResult(asset, 2, 1.0, False, None,
                                              f"AI-generated{mock} {label} visual via adapter (priority 2; labelled AI, "
                                              "not evidence).")
            index.usage[asset.asset_id] += 1
        # remaining illustrative gaps reuse generated images (generate once, reuse intelligently)
        for b in beats:
            if b.selection and b.selection.needs_generation:
                b.selection = _select_with_fallback(index, b.merged_info(), options.allow_unknown_rights, set(),
                                                    options.photo_fallback_for_data_intents)
                if b.selection.asset:
                    index.usage[b.selection.asset.asset_id] += 1
        if len(targets) > cap:
            warnings.append(f"{len(targets) - cap} additional AI image request(s) skipped: plan cap is {cap} (spec §10).")
    if progress:
        progress(0.6, f"AI images generated: {len(generated)}")

    # ---- 5: shots
    shots: list[Shot] = []
    framing_counter: Counter = Counter()
    region_counter: Counter = Counter()
    clip_cursor: dict[str, float] = {}
    shots_per_asset: Counter = Counter()
    change_reasons: Counter = Counter()
    fallback_splits = 0
    for b in beats:
        sel = b.selection
        asset = sel.asset if sel else None
        d = b.end - b.start
        ladder = ladder_for(asset.asset_type) if asset else [("wide", "STATIC")]
        n = 1
        if asset is not None and len(ladder) > 1:
            n = max(1, math.ceil(d / max_shot))
            while n > 1 and d / n < min_shot:
                n -= 1
        if n > 1:
            fallback_splits += n - 1
        pts = _split_points(b, n, min_shot)
        change_reasons[b.change_reason.split(":")[0]] += 1
        last_framing = None
        for k in range(n):
            s0, s1 = round(pts[k], 3), round(pts[k + 1], 3)
            dur = round(s1 - s0, 3)
            overlays: list[dict] = []
            if asset is not None:
                fi = framing_counter[asset.asset_id]
                composition, motion_type = ladder[fi % len(ladder)]
                if (composition, motion_type) == last_framing:
                    fi += 1
                    composition, motion_type = ladder[fi % len(ladder)]
                framing_counter[asset.asset_id] = fi + 1
                meta = asset.extra_metadata or {}
                ridx = region_counter[asset.asset_id]
                if composition == "region":
                    region_counter[asset.asset_id] += 1
                crop = compute_crop(composition, meta, ridx)
                motion = plan_motion(asset.asset_type, motion_type, crop, dur, meta)
                if asset.asset_type == "VIDEO_CLIP":
                    clip = meta.get("clip") or {"in_point": 0.0, "out_point": asset.duration or dur}
                    off = clip_cursor.get(asset.asset_id, 0.0)
                    span = clip["out_point"] - clip["in_point"]
                    if off + dur > span:
                        off = 0.0
                    motion["clip_segment"] = {"in_point": round(clip["in_point"] + off, 3),
                                              "out_point": round(min(clip["out_point"], clip["in_point"] + off + dur), 3)}
                    if dur > span:
                        motion["clip_segment"]["note"] = "clip shorter than shot; renderer should hold last frame or loop"
                    clip_cursor[asset.asset_id] = off + dur
                last_framing = (composition, motion_type)
                if asset.asset_type == "PLACEHOLDER_IMAGE" or asset.generation_kind == "PLACEHOLDER":
                    overlays.append({"type": "editorial_label",
                                     "text": "PLACEHOLDER IMAGE - NOT AI - NOT EVIDENCE"
                                             + (" (FALLBACK)" if asset.is_fallback else ""),
                                     "required": True})
                elif asset.source_type == "AI_GENERATED":
                    overlays.append({"type": "editorial_label",
                                     "text": "AI-GENERATED ILLUSTRATION" + (" (MOCK)" if asset.is_mock else ""),
                                     "required": True})
                if asset.asset_type in ("STAT_CARD", "CHART", "MAP") and asset.attribution:
                    overlays.append({"type": "source_caption", "text": asset.attribution[:300]})
                if asset.rights_status == "UNKNOWN":
                    overlays.append({"type": "rights_warning", "text": "RIGHTS UNKNOWN - clear before publication"})
                reuse_index = shots_per_asset[asset.asset_id]
                shots_per_asset[asset.asset_id] += 1
                status, gap_code = "READY", None
                visual_type, source_type, label = asset.asset_type, asset.source_type, asset.editorial_category
                attribution = asset.attribution
                framing = f"{composition}/{motion['type']}"
            else:
                crop = compute_crop("wide", {})
                motion = {"type": "STATIC", "easing": "linear", "duration": dur}
                status, gap_code = "GAP", (sel.gap_code if sel else "SOURCE_UNAVAILABLE")
                visual_type = b.info["preferred_visuals"][0]
                source_type, label, attribution, reuse_index, framing = "UNKNOWN", "UNKNOWN", None, 0, None
                if sel and sel.needs_generation and not options.generate_ai:
                    gap_reason = sel.editorial_reason.replace("(generation requested)", "(AI generation not enabled for this plan)")
                    sel = SelectionResult(None, 0, 0.0, True, gap_code, gap_reason)
            shot = Shot(
                shot_id=str(uuid.uuid4()),
                visual_plan_id="",
                scene_id=b.scene.scene_id,
                order_index=len(shots),
                start_time=s0, end_time=s1, duration=dur,
                asset_id=asset.asset_id if asset else None,
                visual_type=visual_type, source_type=source_type, editorial_label=label,
                intent=b.info["primary_intent"], status=status, gap_code=gap_code,
                narration_anchor=_anchor(b, s0, s1),
                change_reason=b.change_reason if k == 0 else f"reframe of same asset ({framing}) - spec §11 reuse",
                framing=framing, reuse_index=reuse_index, motion=motion, crop=crop,
                transition=(b.scene.transition or "CUT") if (k == 0 and b.scene_first) else "CUT",
                attribution=attribution, overlays=overlays,
                editorial_reason=sel.editorial_reason if sel else "No selection.",
            )
            shots.append(shot)

    # ---- timing normalisation (Phase 2): the visual timeline must be contiguous. A silence between two
    # narration segments/scenes (e.g. supplied timings with pauses) is covered by HOLDING the previous shot
    # until the next one starts; the hold is recorded on the shot's narration_anchor.
    shots.sort(key=lambda s: (s.start_time, s.order_index))
    gaps_closed, gap_seconds = 0, 0.0
    for prev, nxt in zip(shots, shots[1:]):
        gap = round(nxt.start_time - prev.end_time, 3)
        if gap > 1e-3:
            prev.end_time = nxt.start_time
            prev.duration = round(prev.end_time - prev.start_time, 3)
            if isinstance(prev.motion, dict):
                prev.motion = {**prev.motion, "duration": prev.duration}
            prev.narration_anchor = {**(prev.narration_anchor or {}), "hold_extended_s": gap}
            gaps_closed += 1
            gap_seconds += gap
    for i, s in enumerate(shots):
        s.order_index = i
    timing_sources = sorted({sc.timing_source for sc in scenes})

    # ---- scene bookkeeping
    for scene in scenes:
        s_shots = [s for s in shots if s.scene_id == scene.scene_id]
        scene.shot_ids = [s.shot_id for s in s_shots]
        scene.asset_ids = sorted({s.asset_id for s in s_shots if s.asset_id})
        intents = []
        for s in s_shots:
            if s.intent not in [i["intent"] for i in intents]:
                prefs, ai_ok = INTENT_VISUALS[s.intent]
                intents.append({"intent": s.intent, "preferred_visual": prefs[0],
                                "fallback_visual": prefs[1] if len(prefs) > 1 else None, "ai_allowed": ai_ok})
        scene.visual_intents = intents
    for aid, count in shots_per_asset.items():
        by_id[aid].reuse_count = count

    used = [by_id[a] for a in shots_per_asset]
    gaps = [s for s in shots if s.status == "GAP"]
    total = round(sum(s.duration for s in shots), 3)
    ai_used = [a for a in used if a.source_type == "AI_GENERATED"]
    placeholders_used = [a for a in used if a.asset_type == "PLACEHOLDER_IMAGE" or a.generation_kind == "PLACEHOLDER"]
    gen_assets = [by_id[g] for g in generated]
    stats = {
        "scene_count": len(scenes),
        "beat_count": len(beats),
        "visual_change_count": len(beats),
        "shot_count": len(shots),
        "total_duration_s": total,
        "avg_shot_duration_s": round(total / len(shots), 3) if shots else 0,
        "distinct_asset_count": len(used),
        "source_asset_count": len([a for a in used if a.source_type != "AI_GENERATED" and a not in placeholders_used]),
        "ai_asset_count": len(ai_used),  # AI_GENERATED source type (real AI + MOCK fixtures); never placeholders
        "real_ai_asset_count": len([a for a in ai_used if a.generation_kind == "AI_GENERATED" and not a.is_mock]),
        "placeholder_asset_count": len(placeholders_used),
        # DEPRECATED name (v1.0): all images generated during this plan, of any kind; see the kind-specific fields
        "ai_images_generated_in_plan": len(generated),
        "images_generated_in_plan": len(generated),
        "real_ai_images_generated_in_plan": len([a for a in gen_assets if a.generation_kind == "AI_GENERATED" and not a.is_mock]),
        "placeholder_images_generated_in_plan": len([a for a in gen_assets if a.generation_kind == "PLACEHOLDER"]),
        "fallback_placeholders_in_plan": len([a for a in gen_assets if a.is_fallback]),
        "fixture_images_generated_in_plan": len([a for a in gen_assets if a.is_mock]),
        "timing_sources": timing_sources,
        "timing_gaps_closed": gaps_closed,
        "timing_gap_seconds_closed": round(gap_seconds, 3),
        "mock_assets_used": len([a for a in used if a.is_mock]),
        "reused_asset_count": len([a for a, c in shots_per_asset.items() if c > 1]),
        "max_shots_per_asset": max(shots_per_asset.values()) if shots_per_asset else 0,
        "fallback_duration_splits": fallback_splits,
        "gap_count": len(gaps),
        "gaps_by_code": dict(Counter(s.gap_code for s in gaps)),
        "shots_by_visual_type": dict(Counter(s.visual_type for s in shots)),
        "shots_by_source_type": dict(Counter(s.source_type for s in shots)),
        "visual_change_reasons": dict(change_reasons),
    }
    if gaps:
        warnings.append(f"{len(gaps)} shot(s) are GAPs with no usable asset ({stats['gaps_by_code']}); "
                        "supply source material - AI was not substituted for factual content.")
    if stats["mock_assets_used"]:
        warnings.append("Plan uses MOCK-generated assets (test provider); not for publication.")
    if placeholders_used:
        warnings.append(f"Plan uses {len(placeholders_used)} PLACEHOLDER image(s) (procedural, NOT AI, NOT evidence"
                        f"{'; includes fallback placeholders' if stats['fallback_placeholders_in_plan'] else ''}); "
                        "replace before publication.")
    if "ESTIMATED" in timing_sources:
        warnings.append("Narration timing is ESTIMATED from word count for some scenes; supply timed segments "
                        "(timing_source SUPPLIED) for frame-accurate sync.")
    if any(a.rights_status == "UNKNOWN" for a in used):
        warnings.append("Plan uses assets with UNKNOWN rights (allowed by option); clear rights before publication.")
    if progress:
        progress(0.8, f"planned {len(shots)} shots")
    return PlanOutput(shots=shots, stats=stats, warnings=warnings, generated_asset_ids=generated, assets=by_id)
