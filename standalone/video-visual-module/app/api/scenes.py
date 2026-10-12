from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import scene_out
from app.config import get_settings
from app.db.models import Project, Scene
from app.errors import VideoModuleError
from app.pipeline.import_assets import sanitize_text
from app.pipeline.intent import derive_visual_intents
from app.pipeline.scene_parser import parse_narration, scenes_from_timed_segments
from app.storage import write_json_atomic

router = APIRouter(tags=["scenes"])
TRANSITIONS = {"CUT", "DISSOLVE", "FADE", "FADE_IN", "FADE_OUT", "WIPE"}


class TimedSegment(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    # optional at the schema level so a missing timestamp gets the explicit MISSING_NARRATION_ANCHOR error
    start: float | None = None
    end: float | None = None
    segment_id: str | None = Field(default=None, max_length=64)
    scene: str | None = Field(default=None, max_length=120)


class ScenesCreate(BaseModel):
    project_id: str
    narration: str | None = Field(default=None, description="Plain narration; paragraphs -> scenes, sentences -> segments.")
    segments: list[TimedSegment] | None = Field(default=None, max_length=5000,
                                                description="Timed segments from an external timing contract (spec §30).")
    words_per_minute: float | None = Field(default=None, ge=60, le=400)
    start_offset: float = Field(default=0.0, ge=0)
    replace_existing: bool = Field(default=False, description="Delete the project's existing scenes first.")
    transition: str = "CUT"


@router.post("/scenes", status_code=201)
def create_scenes(body: ScenesCreate, db: Session = Depends(get_db)):
    if db.get(Project, body.project_id) is None:
        raise VideoModuleError("NOT_FOUND", f"Project {body.project_id} not found.")
    if bool(body.narration) == bool(body.segments):
        raise VideoModuleError("VALIDATION_ERROR", "Provide exactly one of 'narration' or 'segments'.")
    if body.transition not in TRANSITIONS:
        raise VideoModuleError("VALIDATION_ERROR", f"transition must be one of {sorted(TRANSITIONS)}")
    existing = db.query(Scene).filter(Scene.project_id == body.project_id)
    # Audit fix: validate/parse BEFORE deleting, so a rejected request (e.g. INVALID_TIMING) never destroys
    # the project's existing scenes. The delete and the inserts are committed together below.
    if body.replace_existing:
        base_order, offset = 0, body.start_offset
        prefix_n = 0
    else:
        base_order = existing.count()
        last_end = db.query(func.max(Scene.end_time)).filter(Scene.project_id == body.project_id).scalar()
        offset = max(body.start_offset, last_end or 0.0)
        prefix_n = base_order
    prefix = f"seg{prefix_n:03d}" if prefix_n else "seg"
    if body.narration:
        drafts = parse_narration(body.narration, wpm=body.words_per_minute or get_settings().words_per_minute,
                                 start_offset=offset, segment_prefix=prefix)
    else:
        drafts = scenes_from_timed_segments([s.model_dump() for s in body.segments], segment_prefix=prefix)
        if not body.replace_existing and base_order and drafts[0].start_time < offset - 1e-3:
            raise VideoModuleError("VALIDATION_ERROR",
                                   "Timed segments overlap existing scenes; use replace_existing or later timings.")
    if body.replace_existing:
        existing.delete()
    created = []
    for i, d in enumerate(drafts):
        sc = Scene(
            project_id=body.project_id, order_index=base_order + i, title=sanitize_text(d.title, 255),
            start_time=d.start_time, end_time=d.end_time, narration_text=d.narration_text,
            narration_segments=[s.as_dict() for s in d.segments],
            narration_segment_ids=[s.segment_id for s in d.segments], timing_source=d.timing_source,
            visual_intents=derive_visual_intents(d.narration_text),
            transition=("FADE_IN" if base_order + i == 0 and body.transition == "CUT" else body.transition),
            importance=d.importance if base_order == 0 else "NORMAL",
        )
        db.add(sc)
        created.append(sc)
    db.commit()
    for sc in created:
        write_json_atomic(get_settings().scenes_dir / f"{sc.scene_id}.json", scene_out(sc))
    return {"project_id": body.project_id, "scene_count": len(created), "scenes": [scene_out(s) for s in created]}


@router.get("/scenes")
def list_scenes(project_id: str, db: Session = Depends(get_db)):
    rows = db.query(Scene).filter(Scene.project_id == project_id).order_by(Scene.start_time, Scene.order_index).all()
    return [scene_out(s) for s in rows]


@router.get("/scenes/{scene_id}")
def get_scene(scene_id: str, db: Session = Depends(get_db)):
    sc = db.get(Scene, scene_id)
    if sc is None:
        raise VideoModuleError("NOT_FOUND", f"Scene {scene_id} not found.")
    return scene_out(sc)
