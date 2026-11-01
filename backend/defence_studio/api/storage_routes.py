"""Storage lifecycle HTTP API (contract: docs/AI_PARALLEL_WORK_CONTROL.md §7)."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from defence_studio.storage.lifecycle import StorageError, StorageLifecycleService


class RetentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: dict[str, int | float | str | None] = Field(min_length=1, max_length=64)


class CleanupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run_id: str = Field(min_length=1, max_length=80)
    confirm: str = Field(min_length=1, max_length=80)


def register_storage_routes(app: FastAPI, require_owner, service: StorageLifecycleService) -> None:
    def run_call(func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except StorageError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    @app.get("/api/storage/summary")
    def storage_summary(owner_id: str = Depends(require_owner)):
        return run_call(service.summary)

    @app.get("/api/storage/render-controls")
    def storage_render_controls(owner_id: str = Depends(require_owner)):
        return run_call(service.render_controls)

    @app.get("/api/storage/capacity")
    def storage_capacity(required_bytes: int = Query(default=0, ge=0), owner_id: str = Depends(require_owner)):
        return run_call(service.capacity, required_bytes)

    @app.get("/api/storage/jobs/{job_id}")
    def storage_job(job_id: str, owner_id: str = Depends(require_owner)):
        return run_call(service.job, owner_id, job_id)

    @app.get("/api/storage/retention")
    def storage_retention(owner_id: str = Depends(require_owner)):
        return run_call(service.retention)

    @app.put("/api/storage/retention")
    def storage_update_retention(body: RetentionBody, owner_id: str = Depends(require_owner)):
        return run_call(service.update_retention, owner_id, body.categories)

    @app.post("/api/storage/cleanup/dry-run")
    def storage_dry_run(owner_id: str = Depends(require_owner)):
        return run_call(service.dry_run, owner_id)

    @app.post("/api/storage/cleanup")
    def storage_cleanup(body: CleanupBody, owner_id: str = Depends(require_owner)):
        return run_call(service.execute, owner_id, body.dry_run_id, body.confirm)

    @app.get("/api/storage/cleanup/history")
    def storage_history(limit: int = Query(default=50, ge=1, le=500), owner_id: str = Depends(require_owner)):
        return run_call(service.history, owner_id, limit)
