from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from defence_studio.auth import authenticated_user
from defence_studio.config import PROJECT_ROOT, auth_secret, database_path, storage_root
from defence_studio.database import initialize_database
from defence_studio.services.jobs import JobError, JobService
from defence_studio.services.review import ReviewError, ReviewService
from defence_studio.services.worker import WorkerService


class CreateJobBody(BaseModel):
    topic: str = Field(min_length=1, max_length=1000)
    language: str = Field(default="ta", min_length=2, max_length=20)


class StageTransitionBody(BaseModel):
    status: str
    error: str = Field(default="", max_length=4000)


class ArtifactReferenceBody(BaseModel):
    stage_key: str
    artifact_type: str
    uri: str
    sha256: str
    schema_version: str


class ReviewDecisionBody(BaseModel):
    decision: str
    reviewed_artifact_sha256: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)
    note: str = Field(default="", max_length=4000)


def create_app(
    database_file: Path | None = None,
    signing_secret: str | None = None,
    storage_root_path: Path | None = None,
) -> FastAPI:
    db_path = Path(database_file) if database_file else database_path()
    secret = signing_secret if signing_secret is not None else auth_secret()
    if len(secret.encode()) < 32:
        raise RuntimeError("Set DEFENCE_STUDIO_AUTH_SECRET to a random secret of at least 32 bytes")
    store_root = Path(storage_root_path) if storage_root_path else storage_root()
    jobs = JobService(db_path)
    worker_service = WorkerService(db_path)
    review = ReviewService(db_path, store_root)

    @asynccontextmanager
    async def lifespan(_app):
        initialize_database(db_path)
        worker_service.recover_expired()
        yield

    app = FastAPI(title="Defence Studio API", version="0.1.0", lifespan=lifespan)
    app.state.database_file = db_path

    @app.exception_handler(JobError)
    async def job_error_handler(_request, exc: JobError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(ReviewError)
    async def review_error_handler(_request, exc: ReviewError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.post("/api/jobs", status_code=201)
    def create_job(
        body: CreateJobBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(authenticated_user(secret)),
    ):
        return jobs.create(owner_id, idempotency_key, body.topic, body.language)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, owner_id: str = Depends(authenticated_user(secret))):
        return jobs.get(job_id, owner_id)

    @app.post("/api/jobs/{job_id}/queue")
    def queue_job(job_id: str, owner_id: str = Depends(authenticated_user(secret))):
        worker_service.queue(job_id, owner_id)
        return jobs.get(job_id, owner_id)

    @app.post("/api/jobs/{job_id}/stages/{stage_key}/transitions")
    def transition_stage(
        job_id: str,
        stage_key: str,
        body: StageTransitionBody,
        owner_id: str = Depends(authenticated_user(secret)),
    ):
        return jobs.transition(job_id, owner_id, stage_key, body.status, body.error)

    @app.post("/api/jobs/{job_id}/artifacts", status_code=201)
    def add_artifact(
        job_id: str,
        body: ArtifactReferenceBody,
        owner_id: str = Depends(authenticated_user(secret)),
    ):
        return jobs.add_artifact(
            job_id, owner_id, body.stage_key, body.artifact_type, body.uri,
            body.sha256, body.schema_version,
        )

    @app.get("/api/jobs/{job_id}/review/sources")
    def list_review_sources(
        job_id: str,
        status: str | None = Query(default=None),
        owner_id: str = Depends(authenticated_user(secret)),
    ):
        return review.list_sources(job_id, owner_id, status=status)

    @app.get("/api/jobs/{job_id}/review/sources/{source_id}")
    def get_review_source(job_id: str, source_id: str, owner_id: str = Depends(authenticated_user(secret))):
        return review.get_source(job_id, owner_id, source_id)

    @app.get("/api/jobs/{job_id}/review/sources/{source_id}/history")
    def get_review_source_history(job_id: str, source_id: str, owner_id: str = Depends(authenticated_user(secret))):
        return review.source_history(job_id, owner_id, source_id)

    @app.post("/api/jobs/{job_id}/review/sources/{source_id}/decisions", status_code=201)
    def decide_review_source(
        job_id: str,
        source_id: str,
        body: ReviewDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(authenticated_user(secret)),
    ):
        return review.decide_source(
            job_id, owner_id, source_id, body.decision, body.reviewed_artifact_sha256,
            body.expected_revision, body.note, idempotency_key,
        )

    @app.get("/api/jobs/{job_id}/review/claims")
    def list_review_claims(
        job_id: str,
        status: str | None = Query(default=None),
        conflict: bool = Query(default=False),
        source_id: str | None = Query(default=None),
        owner_id: str = Depends(authenticated_user(secret)),
    ):
        return review.list_claims(job_id, owner_id, status=status, conflict_only=conflict, source_id=source_id)

    @app.get("/api/jobs/{job_id}/review/claims/{claim_id}")
    def get_review_claim(job_id: str, claim_id: str, owner_id: str = Depends(authenticated_user(secret))):
        return review.get_claim(job_id, owner_id, claim_id)

    @app.get("/api/jobs/{job_id}/review/claims/{claim_id}/history")
    def get_review_claim_history(job_id: str, claim_id: str, owner_id: str = Depends(authenticated_user(secret))):
        return review.claim_history(job_id, owner_id, claim_id)

    @app.post("/api/jobs/{job_id}/review/claims/{claim_id}/decisions", status_code=201)
    def decide_review_claim(
        job_id: str,
        claim_id: str,
        body: ReviewDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(authenticated_user(secret)),
    ):
        return review.decide_claim(
            job_id, owner_id, claim_id, body.decision, body.reviewed_artifact_sha256,
            body.expected_revision, body.note, idempotency_key,
        )

    frontend_dir = PROJECT_ROOT / "frontend"
    if frontend_dir.is_dir():
        # Registered last so it never shadows the /api/... routes above:
        # Starlette tries routes in registration order, and this mount only
        # matches requests nothing more specific already claimed.
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


app = create_app()
