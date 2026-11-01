"""Image Research HTTP API. Namespace is independent of content-research jobs."""
from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from defence_studio.image_research.service import ImageResearchError, ImageResearchService


class CreateImageJobBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=2, max_length=300)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    language: str = Field(default="en", min_length=2, max_length=12)
    date_from: str | None = None
    date_to: str | None = None
    categories: list[str] = Field(default_factory=list, max_length=12)
    sources: list[str] = Field(default_factory=lambda: ["wikimedia_commons", "openverse"])
    minimum_unique: int = Field(default=30, ge=1, le=200)
    research_topic_id: str | None = Field(default=None, max_length=80)
    script_id: str | None = Field(default=None, max_length=80)


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(ACCEPT|REJECT|NEEDS_VERIFICATION)$")
    reason: str = Field(default="", max_length=1000)


class BulkReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(ACCEPT|REJECT)$")
    image_ids: list[str] = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=1000)


def register_image_research_routes(app: FastAPI, require_owner, service: ImageResearchService) -> None:
    idem = Header(min_length=1, max_length=200, alias="Idempotency-Key")

    def run_call(func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ImageResearchError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    @app.get("/api/image-research/providers")
    def providers(owner_id: str = Depends(require_owner)):
        return service.providers_status()

    @app.post("/api/image-research/jobs", status_code=201)
    def create_job(body: CreateImageJobBody, idempotency_key: str = idem, owner_id: str = Depends(require_owner)):
        return run_call(service.create_job, owner_id, idempotency_key, body.model_dump())

    @app.get("/api/image-research/jobs")
    def list_jobs(owner_id: str = Depends(require_owner)):
        return service.list_jobs(owner_id)

    @app.get("/api/image-research/jobs/{job_id}")
    def get_job(job_id: str, owner_id: str = Depends(require_owner)):
        return run_call(service.get_job, job_id, owner_id)

    @app.post("/api/image-research/jobs/{job_id}/run")
    def run_job(job_id: str, owner_id: str = Depends(require_owner)):
        return run_call(service.run, job_id, owner_id)

    @app.get("/api/image-research/jobs/{job_id}/candidates")
    def candidates(
        job_id: str,
        source: str | None = None,
        review_status: str | None = None,
        rights_status: str | None = None,
        duplicate_role: str | None = None,
        license: str | None = Query(default=None),
        category: str | None = None,
        owner_id: str = Depends(require_owner),
    ):
        return run_call(service.candidates, job_id, owner_id, {
            "source": source, "review_status": review_status, "rights_status": rights_status,
            "duplicate_role": duplicate_role, "license": license, "category": category,
        })

    @app.get("/api/image-research/jobs/{job_id}/duplicates")
    def duplicates(job_id: str, owner_id: str = Depends(require_owner)):
        return run_call(service.duplicates, job_id, owner_id)

    @app.post("/api/image-research/candidates/{image_id}/review")
    def review(image_id: str, body: ReviewBody, idempotency_key: str = idem, owner_id: str = Depends(require_owner)):
        del idempotency_key
        return run_call(service.review, image_id, owner_id, body.action, body.reason)

    @app.post("/api/image-research/jobs/{job_id}/reviews/bulk")
    def bulk(job_id: str, body: BulkReviewBody, idempotency_key: str = idem, owner_id: str = Depends(require_owner)):
        del idempotency_key
        return run_call(service.bulk_review, job_id, owner_id, body.action, body.image_ids, body.reason)

    @app.get("/api/image-research/jobs/{job_id}/export")
    def export(job_id: str, owner_id: str = Depends(require_owner)):
        return run_call(service.export_manifest, job_id, owner_id)

    @app.get("/api/image-research/candidates/{image_id}/preview")
    def preview(image_id: str, owner_id: str = Depends(require_owner)):
        path = run_call(service.preview_path, image_id, owner_id)
        return FileResponse(path)
