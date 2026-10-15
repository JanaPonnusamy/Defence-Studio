"""HTTP routes for fact-check and media-context verification."""

from __future__ import annotations

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field


class ClaimsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list = Field(default_factory=list)
    sources: list = Field(default_factory=list)
    collection: dict = Field(default_factory=lambda: {"attempted": True, "errors": []})


class PackageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1, max_length=300)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=4000)


class OverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str
    claim_id: str
    verdict: str
    note: str = Field(min_length=1, max_length=4000)
    accepted_for_script: bool = False


def register_factcheck_routes(app: FastAPI, require_owner, service) -> None:
    idem = Header(min_length=1, max_length=200, alias="Idempotency-Key")

    @app.get("/api/factcheck/providers")
    def providers(owner_id: str = Depends(require_owner)):
        return service.providers()

    @app.post("/api/jobs/{subject_job_id}/factcheck/claims", status_code=202)
    def run_claims(subject_job_id: str, body: ClaimsBody, idempotency_key: str = idem,
                   owner_id: str = Depends(require_owner)):
        job = service.enqueue(owner_id, idempotency_key, subject_job_id, "claims", body.model_dump())
        if job["status"] == "QUEUED":
            service.run_pending()
        return service.get_job(job["job_id"], owner_id)

    @app.post("/api/jobs/{subject_job_id}/factcheck/media", status_code=202)
    async def run_media(
        subject_job_id: str,
        file: UploadFile = File(...),
        claim_json: str = Form("{}"),
        known_json: str = Form("[]"),
        live_reverse_search: bool = Form(False),
        idempotency_key: str = idem,
        owner_id: str = Depends(require_owner),
    ):
        import json
        raw = await file.read()
        try:
            claim = json.loads(claim_json)
            known = json.loads(known_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, f"Media form JSON is invalid: {exc}") from exc
        path = service.store_upload(raw, file.filename or "upload")
        job = service.enqueue(owner_id, idempotency_key, subject_job_id, "media", {
            "stored_path": str(path), "filename": file.filename or "upload", "claim": claim, "known": known,
            # explicit per-request opt-in; absent (default) means the provider is never called
            **({"live_reverse_search": True} if live_reverse_search else {}),
        })
        if job["status"] == "QUEUED":
            service.run_pending()
        return service.get_job(job["job_id"], owner_id)

    @app.post("/api/jobs/{subject_job_id}/factcheck/package", status_code=202)
    def run_package(subject_job_id: str, body: PackageBody, idempotency_key: str = idem,
                    owner_id: str = Depends(require_owner)):
        job = service.enqueue(owner_id, idempotency_key, subject_job_id, "package", body.model_dump())
        if job["status"] == "QUEUED":
            service.run_pending()
        return service.get_job(job["job_id"], owner_id)

    @app.get("/api/jobs/{subject_job_id}/factcheck/package")
    def get_package(subject_job_id: str, owner_id: str = Depends(require_owner)):
        return service.latest_package(subject_job_id, owner_id)

    @app.get("/api/jobs/{subject_job_id}/factcheck/history")
    def history(subject_job_id: str, owner_id: str = Depends(require_owner)):
        return service.history(subject_job_id, owner_id)

    @app.post("/api/jobs/{subject_job_id}/factcheck/override")
    def override(subject_job_id: str, body: OverrideBody, owner_id: str = Depends(require_owner)):
        return service.override_claim(
            subject_job_id, owner_id, body.package_id, body.claim_id, body.verdict, body.note,
            body.accepted_for_script,
        )

    @app.post("/api/jobs/{subject_job_id}/factcheck/approve")
    def approve(subject_job_id: str, body: ApproveBody, owner_id: str = Depends(require_owner)):
        return service.approve(subject_job_id, owner_id, body.package_id, body.note)

    @app.post("/api/factcheck/jobs/{job_id}/cancel")
    def cancel(job_id: str, owner_id: str = Depends(require_owner)):
        return service.cancel(job_id, owner_id)

    @app.get("/api/factcheck/jobs/{job_id}")
    def get_job(job_id: str, owner_id: str = Depends(require_owner)):
        return service.get_job(job_id, owner_id)
