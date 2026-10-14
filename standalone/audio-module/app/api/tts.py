from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.models import Job, PerformancePlan
from app.jobs.handlers import tts_synthesize_job
from app.jobs.queue import get_job_queue
from app.schemas.io import JobOut, TTSSynthesizeRequest
from app.tts.registry import list_adapters

router = APIRouter(tags=["tts"])


@router.get("/tts/providers")
def get_tts_providers():
    return {"providers": list_adapters()}


@router.post("/tts/synthesize", response_model=JobOut)
def synthesize(req: TTSSynthesizeRequest, db: Session = Depends(get_session)):
    performance_plan = []
    if req.performance_plan_id:
        plan = db.get(PerformancePlan, req.performance_plan_id)
        if plan:
            performance_plan = plan.instructions

    job = Job(job_type="tts_synthesize", status="QUEUED", payload={
        "text": req.text, "voice_id": req.voice_id, "language": req.language,
        "performance_plan_id": req.performance_plan_id, "performance_plan": performance_plan,
        "provider": req.provider, "output_format": req.output_format,
    })
    db.add(job)
    db.commit()
    db.refresh(job)
    get_job_queue().submit(job.job_id, tts_synthesize_job, job.payload)
    return job
