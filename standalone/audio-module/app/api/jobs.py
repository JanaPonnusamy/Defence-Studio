from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.models import Job
from app.errors import NotFoundError
from app.schemas.io import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_session)):
    job = db.get(Job, job_id)
    if not job:
        raise NotFoundError(f"Job {job_id} not found")
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_session)):
    return db.query(Job).order_by(Job.created_at.desc()).limit(100).all()
