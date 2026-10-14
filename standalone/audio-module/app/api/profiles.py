from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.models import Job, PerformancePlan, Profile
from app.errors import NotFoundError
from app.jobs.handlers import build_profile_job
from app.jobs.queue import get_job_queue
from app.pipeline.planner import build_performance_plan
from app.schemas.io import BuildProfileRequest, JobOut, PerformancePlanRequest, ProfileOut

router = APIRouter(tags=["profiles"])


@router.post("/profiles/build", response_model=JobOut)
def build_profile_endpoint(req: BuildProfileRequest, db: Session = Depends(get_session)):
    job = Job(job_type="build_profile", status="QUEUED",
              payload={"profile_name": req.profile_name, "analysis_ids": req.analysis_ids})
    db.add(job)
    db.commit()
    db.refresh(job)
    get_job_queue().submit(job.job_id, build_profile_job, job.payload)
    return job


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_session)):
    return db.query(Profile).order_by(Profile.created_at.desc()).all()


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: str, db: Session = Depends(get_session)):
    p = db.get(Profile, profile_id)
    if not p:
        raise NotFoundError(f"Profile {profile_id} not found")
    return p


@router.post("/profiles/plan")
def build_plan_endpoint(req: PerformancePlanRequest, db: Session = Depends(get_session)):
    profile = db.get(Profile, req.profile_id)
    if not profile:
        raise NotFoundError(f"Profile {req.profile_id} not found")
    profile_dict = {
        "tempo": profile.tempo, "pause_style": profile.pause_style, "energy": profile.energy,
        "emphasis": profile.emphasis, "pitch_style": profile.pitch_style, "transition_pause": profile.transition_pause,
    }
    instructions = build_performance_plan(req.script, profile_dict, req.language)
    plan = PerformancePlan(profile_id=profile.profile_id, script_text=req.script, language=req.language, instructions=instructions)
    db.add(plan)
    db.commit()
    return {"plan_id": plan.plan_id, "profile_id": profile.profile_id, "instructions": instructions}


@router.get("/profiles/plan/{plan_id}")
def get_plan(plan_id: str, db: Session = Depends(get_session)):
    plan = db.get(PerformancePlan, plan_id)
    if not plan:
        raise NotFoundError(f"Plan {plan_id} not found")
    return {"plan_id": plan.plan_id, "profile_id": plan.profile_id, "instructions": plan.instructions}
