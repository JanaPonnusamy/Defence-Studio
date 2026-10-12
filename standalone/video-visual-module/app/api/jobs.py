from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import job_out
from app.db.models import Job
from app.errors import VideoModuleError
from app.jobs.queue import JOB_STATUSES, cancel_job, retry_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(project_id: str | None = None, status: str | None = None, job_type: str | None = None,
              limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Job)
    if project_id:
        q = q.filter(Job.project_id == project_id)
    if status:
        if status not in JOB_STATUSES:
            raise VideoModuleError("VALIDATION_ERROR", f"status must be one of {JOB_STATUSES}")
        q = q.filter(Job.status == status)
    if job_type:
        q = q.filter(Job.job_type == job_type)
    return [job_out(j) for j in q.order_by(Job.created_at.desc()).limit(max(1, min(1000, limit))).all()]


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise VideoModuleError("NOT_FOUND", f"Job {job_id} not found.")
    return job_out(job)


@router.post("/{job_id}/cancel")
def cancel(job_id: str, db: Session = Depends(get_db)):
    return job_out(cancel_job(db, job_id))


@router.post("/{job_id}/retry", status_code=202)
def retry(job_id: str, db: Session = Depends(get_db)):
    return job_out(retry_job(db, job_id))
