from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

from defence_studio.auth import authenticated_user, mint_dev_session_token
from defence_studio.config import PROJECT_ROOT, auth_secret, database_path, storage_root
from defence_studio.dev_auth import DevAuthConfig, load_dev_auth_config, request_is_loopback
from defence_studio.database import initialize_database
from defence_studio.services.discovery import DiscoveryApiService, DiscoveryError
from defence_studio.services.jobs import JobError, JobService
from defence_studio.services.review import ReviewError, ReviewService
from defence_studio.services.worker import WorkerService
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.script import ScriptService
from defence_studio.services.scene import SceneService
from defence_studio.services.assets import AssetService
from defence_studio.contracts.evidence import ApprovalBody
from defence_studio.contracts.script import GenerationConfigV1
from defence_studio.contracts.scene import ScenePlanConfigV1


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
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: str
    reviewed_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=4000)


class DiscoveryRequestBody(BaseModel):
    topic: str = Field(min_length=1, max_length=1000)
    language: str = Field(default="en", min_length=2, max_length=20)
    search_terms: list[str] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    limits: dict = Field(default_factory=dict)


class DiscoverySelectionBody(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=50)


class ScriptReviewDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str = Field(min_length=1, max_length=4000)


class ScriptApprovalRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(min_length=1, max_length=4000)


class SceneReviseBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_version: int = Field(gt=0)
    # Free-form edit instructions (reorder/remove/update/add); the service
    # validates every operation and re-validates each touched scene.
    edits: dict = Field(min_length=1)


class ScenePlanApprovalRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(min_length=1, max_length=4000)


class AssetRegisterBody(BaseModel):
    # The descriptor body is validated by the M2.0 contract in the service (which
    # computes the content-addressed asset_id); accept it as a free object here.
    model_config = ConfigDict(extra="allow")


class AssetRightsBody(BaseModel):
    model_config = ConfigDict(extra="allow")


class AssetAssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(pattern=r"^(ta|en)$")
    asset_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_use: str | None = None


class AssetReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    reviewed_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_rights_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=4000)


def create_app(
    database_file: Path | None = None,
    signing_secret: str | None = None,
    storage_root_path: Path | None = None,
    dev_auth: DevAuthConfig | None = None,
) -> FastAPI:
    db_path = Path(database_file) if database_file else database_path()
    secret = signing_secret if signing_secret is not None else auth_secret()
    if len(secret.encode()) < 32:
        raise RuntimeError("Set DEFENCE_STUDIO_AUTH_SECRET to a random secret of at least 32 bytes")
    store_root = Path(storage_root_path) if storage_root_path else storage_root()
    # Dev-auth bypass is opt-in and off by default (``load_dev_auth_config``
    # returns a disabled config unless both DEV_MODE and DEV_AUTH_BYPASS are
    # set). Tests may inject an explicit config; production leaves it ``None``
    # so the environment decides.
    dev_config = dev_auth if dev_auth is not None else load_dev_auth_config()
    # One shared dependency instance: production path when the bypass is unsafe,
    # loopback bypass when it is safe. Reused across every authed route below.
    require_owner = authenticated_user(secret, dev_config)
    jobs = JobService(db_path)
    worker_service = WorkerService(db_path)
    review = ReviewService(db_path, store_root)
    discovery = DiscoveryApiService(db_path, store_root)
    evidence = EvidenceService(db_path, store_root)
    scripts = ScriptService(db_path, store_root)
    scenes = SceneService(db_path, store_root)
    assets = AssetService(db_path, store_root)

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

    @app.exception_handler(DiscoveryError)
    async def discovery_error_handler(_request, exc: DiscoveryError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    # ScriptError is not a distinct base: ScriptService raises the ReviewError
    # family (ScriptGenerationFailed/ScriptNotFound/ScriptValidationError/
    # ScriptStaleRevision all derive from ReviewError or its subclasses), so
    # the existing review_error_handler above already covers every Phase 3
    # error path with the correct status codes.

    @app.get("/api/dev/session")
    def dev_session(request: Request):
        """Mint a short-lived bearer token for local development.

        Returns a token **only** when the dev-auth bypass is safely configured
        (opt-in env flags) *and* the request is a direct loopback request with
        no proxy headers. In every other configuration it responds 404 -- it is
        not an unauthenticated public diagnostic endpoint, and it never reveals
        whether the server is in dev mode to a non-loopback caller. The signing
        secret is never returned; only a normal token the UI can use.
        """
        loopback = request_is_loopback(
            request.client.host if request.client else None, request.headers
        )
        if not (dev_config.safe and loopback):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        token = mint_dev_session_token(secret, dev_config.owner_id, minutes=60)
        return {
            "owner_id": dev_config.owner_id,
            "token": token,
            "token_type": "Bearer",
            "mode": "development",
        }

    @app.post("/api/jobs", status_code=201)
    def create_job(
        body: CreateJobBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return jobs.create(owner_id, idempotency_key, body.topic, body.language)

    @app.get("/api/jobs")
    def list_jobs(owner_id: str = Depends(require_owner)):
        """List the authenticated owner's jobs (newest first).

        Owner-scoped, so dashboard job selection is backed by real persisted
        state while ownership isolation is preserved.
        """
        return {"jobs": jobs.list_for_owner(owner_id)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, owner_id: str = Depends(require_owner)):
        return jobs.get(job_id, owner_id)

    @app.post("/api/jobs/{job_id}/queue")
    def queue_job(job_id: str, owner_id: str = Depends(require_owner)):
        worker_service.queue(job_id, owner_id)
        return jobs.get(job_id, owner_id)

    @app.post("/api/jobs/{job_id}/stages/{stage_key}/transitions")
    def transition_stage(
        job_id: str,
        stage_key: str,
        body: StageTransitionBody,
        owner_id: str = Depends(require_owner),
    ):
        return jobs.transition(job_id, owner_id, stage_key, body.status, body.error)

    @app.post("/api/jobs/{job_id}/artifacts", status_code=201)
    def add_artifact(
        job_id: str,
        body: ArtifactReferenceBody,
        owner_id: str = Depends(require_owner),
    ):
        return jobs.add_artifact(
            job_id, owner_id, body.stage_key, body.artifact_type, body.uri,
            body.sha256, body.schema_version,
        )

    @app.get("/api/jobs/{job_id}/review/sources")
    def list_review_sources(
        job_id: str,
        status: str | None = Query(default=None),
        owner_id: str = Depends(require_owner),
    ):
        return review.list_sources(job_id, owner_id, status=status)

    @app.get("/api/jobs/{job_id}/review/sources/{source_id}")
    def get_review_source(job_id: str, source_id: str, owner_id: str = Depends(require_owner)):
        return review.get_source(job_id, owner_id, source_id)

    @app.get("/api/jobs/{job_id}/review/sources/{source_id}/history")
    def get_review_source_history(job_id: str, source_id: str, owner_id: str = Depends(require_owner)):
        return review.source_history(job_id, owner_id, source_id)

    @app.post("/api/jobs/{job_id}/review/sources/{source_id}/decisions", status_code=201)
    def decide_review_source(
        job_id: str,
        source_id: str,
        body: ReviewDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
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
        owner_id: str = Depends(require_owner),
    ):
        return review.list_claims(job_id, owner_id, status=status, conflict_only=conflict, source_id=source_id)

    @app.get("/api/jobs/{job_id}/review/claims/{claim_id}")
    def get_review_claim(job_id: str, claim_id: str, owner_id: str = Depends(require_owner)):
        return review.get_claim(job_id, owner_id, claim_id)

    @app.get("/api/jobs/{job_id}/review/claims/{claim_id}/history")
    def get_review_claim_history(job_id: str, claim_id: str, owner_id: str = Depends(require_owner)):
        return review.claim_history(job_id, owner_id, claim_id)

    @app.post("/api/jobs/{job_id}/review/claims/{claim_id}/decisions", status_code=201)
    def decide_review_claim(
        job_id: str,
        claim_id: str,
        body: ReviewDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return review.decide_claim(
            job_id, owner_id, claim_id, body.decision, body.reviewed_artifact_sha256,
            body.expected_revision, body.note, idempotency_key,
        )

    @app.post("/api/jobs/{job_id}/discovery/requests", status_code=201)
    def create_discovery_request(
        job_id: str,
        body: DiscoveryRequestBody,
        owner_id: str = Depends(require_owner),
    ):
        return discovery.create_request(job_id, owner_id, body.model_dump())

    @app.get("/api/jobs/{job_id}/discovery/requests")
    def get_discovery_request(job_id: str, owner_id: str = Depends(require_owner)):
        return discovery.get_request(job_id, owner_id)

    @app.get("/api/jobs/{job_id}/discovery/candidates")
    def list_discovery_candidates(
        job_id: str,
        language: str | None = Query(default=None),
        source_type: str | None = Query(default=None),
        provider: str | None = Query(default=None),
        min_score: float | None = Query(default=None),
        query: str | None = Query(default=None),
        owner_id: str = Depends(require_owner),
    ):
        return discovery.list_candidates(
            job_id, owner_id, language=language, source_type=source_type,
            provider=provider, min_score=min_score, query=query,
        )

    @app.get("/api/jobs/{job_id}/discovery/candidates/{candidate_id}")
    def get_discovery_candidate(job_id: str, candidate_id: str, owner_id: str = Depends(require_owner)):
        return discovery.get_candidate(job_id, owner_id, candidate_id)

    @app.post("/api/jobs/{job_id}/discovery/selection", status_code=201)
    def select_discovery_candidates(
        job_id: str,
        body: DiscoverySelectionBody,
        owner_id: str = Depends(require_owner),
    ):
        return discovery.select_candidates(job_id, owner_id, body.candidate_ids)

    @app.get("/api/jobs/{job_id}/discovery/selection")
    def get_discovery_selection(job_id: str, owner_id: str = Depends(require_owner)):
        return discovery.get_selection(job_id, owner_id)

    # -- Phase 1D / Phase 2: evidence timeline, package, readiness, approval,
    # export. One canonical /package/* + /timeline surface (the routes named in
    # the core design) delegating to the single EvidenceService engine; no
    # duplicate /evidence/* tree. EvidenceService raises ReviewError subclasses
    # (ReviewNotFound/ReviewValidationError/StaleRevision), handled above.

    @app.get("/api/jobs/{job_id}/timeline")
    def get_timeline(
        job_id: str,
        start: str | None = None,
        end: str | None = None,
        source_id: str | None = None,
        claim_id: str | None = None,
        language: str | None = None,
        owner_id: str = Depends(require_owner),
    ):
        return evidence.timeline(job_id, owner_id, start, end, source_id, claim_id, language)

    @app.get("/api/jobs/{job_id}/package")
    def get_package(job_id: str, owner_id: str = Depends(require_owner)):
        return evidence.draft(job_id, owner_id)

    @app.get("/api/jobs/{job_id}/package/readiness")
    def get_package_readiness(job_id: str, owner_id: str = Depends(require_owner)):
        state = evidence.draft(job_id, owner_id)
        package = state["package"]
        return {
            "job_id": job_id,
            "revision": state["revision"],
            "ready": package["ready"],
            "blockers": package["blockers"],
            "limitations": package["limitations"],
            "eligible_claim_ids": package["eligible_claim_ids"],
            "current_approval": state["current_approval"],
        }

    @app.post("/api/jobs/{job_id}/package/approve", status_code=201)
    def approve_package(
        job_id: str,
        body: ApprovalBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return evidence.approve(job_id, owner_id, body.revision, body.note, idempotency_key)

    @app.get("/api/jobs/{job_id}/package/export")
    def export_package(job_id: str, draft: bool = False, owner_id: str = Depends(require_owner)):
        return JSONResponse(evidence.export(job_id, owner_id, draft), headers={
            "Content-Disposition": 'attachment; filename="evidence-package.json"',
            "Cache-Control": "no-store",
        })

    # -- Phase 3: source-grounded script generation, evidence validation,
    # review, and approval. Human-paced like F5/Phase 2 (synchronous HTTP, not
    # a worker stage); generation is only ever triggered by an explicit POST,
    # never during page load, package inspection, or job creation/review.

    @app.get("/api/jobs/{job_id}/script/revisions")
    def list_script_revisions(
        job_id: str,
        language: str | None = Query(default=None),
        owner_id: str = Depends(require_owner),
    ):
        return scripts.list_revisions(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/script/revisions/{language}/{version}")
    def get_script_revision(
        job_id: str, language: str, version: int,
        owner_id: str = Depends(require_owner),
    ):
        return scripts.get_revision(job_id, owner_id, language, version)

    @app.get("/api/jobs/{job_id}/script/revisions/{language}/{version}/history")
    def get_script_revision_history(
        job_id: str, language: str, version: int,
        owner_id: str = Depends(require_owner),
    ):
        return scripts.revision_history(job_id, owner_id, language, version)

    @app.post("/api/jobs/{job_id}/script/generate", status_code=201)
    def generate_script(
        job_id: str,
        body: GenerationConfigV1,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return scripts.generate(job_id, owner_id, body.model_dump(), idempotency_key)

    @app.post("/api/jobs/{job_id}/script/revisions/{language}/{version}/request-changes", status_code=201)
    def request_script_changes(
        job_id: str, language: str, version: int,
        body: ScriptReviewDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return scripts.request_changes(job_id, owner_id, language, version, body.note, idempotency_key)

    @app.post("/api/jobs/{job_id}/script/revisions/{language}/{version}/approve", status_code=201)
    def approve_script_revision(
        job_id: str, language: str, version: int,
        body: ScriptApprovalRequestBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return scripts.approve(job_id, owner_id, language, version, body.revision_sha256, body.note, idempotency_key)

    @app.get("/api/jobs/{job_id}/script/export/{language}")
    def export_approved_script(
        job_id: str, language: str,
        owner_id: str = Depends(require_owner),
    ):
        return JSONResponse(scripts.export_approved(job_id, owner_id, language), headers={
            "Content-Disposition": f'attachment; filename="script-{language}-approved.json"',
            "Cache-Control": "no-store",
        })

    # Phase 4 M1 scene planning/persistence/editing/approval. Human-paced like
    # Phase 3 (synchronous HTTP, not a worker stage). `generate` is deterministic
    # and offline -- it never calls a text/TTS/alignment/render provider; it only
    # derives scenes from the already-approved script export. SceneService raises
    # the ReviewError family, so review_error_handler maps every status code.

    @app.get("/api/jobs/{job_id}/scene/plans")
    def list_scene_plans(
        job_id: str,
        language: str | None = Query(default=None),
        owner_id: str = Depends(require_owner),
    ):
        return scenes.list_plans(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/scene/plans/{language}/{version}")
    def get_scene_plan(
        job_id: str, language: str, version: int,
        owner_id: str = Depends(require_owner),
    ):
        return scenes.get_plan(job_id, owner_id, language, version)

    @app.get("/api/jobs/{job_id}/scene/plans/{language}/{version}/history")
    def get_scene_plan_history(
        job_id: str, language: str, version: int,
        owner_id: str = Depends(require_owner),
    ):
        return scenes.plan_history(job_id, owner_id, language, version)

    @app.get("/api/jobs/{job_id}/scene/plans/{language}/{version}/scene-digests")
    def get_scene_plan_scene_digests(
        job_id: str, language: str, version: int,
        owner_id: str = Depends(require_owner),
    ):
        return scenes.scene_digests(job_id, owner_id, language, version)

    @app.post("/api/jobs/{job_id}/scene/generate", status_code=201)
    def generate_scene_plan(
        job_id: str,
        body: ScenePlanConfigV1,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return scenes.generate(job_id, owner_id, body.model_dump(), idempotency_key)

    @app.post("/api/jobs/{job_id}/scene/plans/{language}/revise", status_code=201)
    def revise_scene_plan(
        job_id: str, language: str,
        body: SceneReviseBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return scenes.revise(job_id, owner_id, language, body.base_version, body.edits, idempotency_key)

    @app.post("/api/jobs/{job_id}/scene/plans/{language}/{version}/request-changes", status_code=201)
    def request_scene_plan_changes(
        job_id: str, language: str, version: int,
        body: ScriptReviewDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return scenes.request_changes(job_id, owner_id, language, version, body.note, idempotency_key)

    @app.post("/api/jobs/{job_id}/scene/plans/{language}/{version}/approve", status_code=201)
    def approve_scene_plan(
        job_id: str, language: str, version: int,
        body: ScenePlanApprovalRequestBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return scenes.approve(job_id, owner_id, language, version, body.plan_sha256, body.note, idempotency_key)

    # Phase 5 M2.1 asset management & rights provenance. Human-paced, synchronous,
    # reference-only (no download/fetch). AssetService raises the ReviewError
    # family so the existing handler maps 404/409/422. Registered before the
    # static mount so none of these are shadowed.
    @app.post("/api/jobs/{job_id}/assets", status_code=201)
    def register_asset(
        job_id: str, body: AssetRegisterBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return assets.register_asset(job_id, owner_id, body.model_dump(), idempotency_key)

    @app.get("/api/jobs/{job_id}/assets")
    def list_assets(
        job_id: str, review_status: str | None = None, effective_rights: str | None = None,
        kind: str | None = None, owner_id: str = Depends(require_owner),
    ):
        return assets.list_assets(job_id, owner_id, review_status=review_status,
                                  effective_rights=effective_rights, kind=kind)

    @app.get("/api/jobs/{job_id}/assets/{asset_id}")
    def get_asset(job_id: str, asset_id: str, owner_id: str = Depends(require_owner)):
        return assets.get_asset(job_id, owner_id, asset_id)

    @app.post("/api/jobs/{job_id}/assets/{asset_id}/rights", status_code=201)
    def set_asset_rights(
        job_id: str, asset_id: str, body: AssetRightsBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return assets.set_rights(job_id, owner_id, asset_id, body.model_dump(), idempotency_key)

    @app.post("/api/jobs/{job_id}/assets/{asset_id}/review", status_code=201)
    def review_asset(
        job_id: str, asset_id: str, body: AssetReviewBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return assets.review_asset(job_id, owner_id, asset_id, body.decision,
                                   body.reviewed_asset_sha256, body.reviewed_rights_sha256,
                                   body.expected_revision, body.note, idempotency_key)

    @app.get("/api/jobs/{job_id}/assets/{asset_id}/history")
    def asset_history(job_id: str, asset_id: str, owner_id: str = Depends(require_owner)):
        return assets.asset_review_history(job_id, owner_id, asset_id)

    @app.post("/api/jobs/{job_id}/scenes/{scene_id}/assets", status_code=201)
    def assign_asset(
        job_id: str, scene_id: str, body: AssetAssignBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return assets.assign_asset(job_id, owner_id, body.language, scene_id, body.asset_id,
                                   idempotency_key, target_use=body.target_use)

    @app.post("/api/jobs/{job_id}/scenes/{scene_id}/assets/{asset_id}/remove")
    def remove_assignment(
        job_id: str, scene_id: str, asset_id: str, language: str,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return assets.remove_assignment(job_id, owner_id, language, scene_id, asset_id, idempotency_key)

    @app.get("/api/jobs/{job_id}/assignments")
    def list_assignments(
        job_id: str, language: str, owner_id: str = Depends(require_owner),
    ):
        return assets.list_assignments(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/assignments/readiness")
    def assignment_readiness(
        job_id: str, language: str, owner_id: str = Depends(require_owner),
    ):
        return assets.assignment_readiness(job_id, owner_id, language)

    frontend_dir = PROJECT_ROOT / "frontend"
    if frontend_dir.is_dir():
        # Registered last so it never shadows the /api/... routes above:
        # Starlette tries routes in registration order, and this mount only
        # matches requests nothing more specific already claimed.
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


app = create_app()
