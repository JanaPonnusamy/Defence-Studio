"""Standalone FastAPI app for the word-level audio alignment component.

Starts and serves even when the real alignment model is absent: ``/health``
and ``/capabilities`` report real alignment as unavailable rather than faking
results. All request-supplied paths are confined to the configured input root
(the API never exposes arbitrary filesystem access). Errors render with a
consistent ``{"detail", "code"}`` shape and the mapped status code.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from align_engine import __version__
from align_engine.audio import resolve_input_path
from align_engine.config import AlignConfig
from align_engine.contracts import AlignmentRequestV1
from align_engine.engines import default_registry
from align_engine.errors import AlignError
from align_engine.jobs import JobStore


def create_app(config: AlignConfig | None = None) -> FastAPI:
    cfg = config or AlignConfig.from_env()
    registry = default_registry()
    store = JobStore(cfg, registry=registry)

    @asynccontextmanager
    async def lifespan(_app):
        # Crash recovery on startup; never downloads a model or hits a network.
        store.recover_interrupted()
        yield

    app = FastAPI(title="Defence Align — word-level audio alignment",
                  version=__version__, lifespan=lifespan)
    app.state.config = cfg
    app.state.store = store

    @app.exception_handler(AlignError)
    async def _align_error(_request, exc: AlignError):
        content = {"detail": str(exc), "code": exc.code}
        if getattr(exc, "issues", None):
            content["issues"] = exc.issues
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.get("/health")
    def health():
        real = registry.get("real").capabilities()
        return {
            "status": "ok",
            "version": __version__,
            "real_alignment_available": real.available,
            "real_alignment_note": real.notes,
        }

    @app.get("/capabilities")
    def capabilities():
        return {"engines": registry.capabilities(),
                "supported_languages_declared": ["ta", "en"]}

    @app.post("/alignments/validate")
    def validate(request: AlignmentRequestV1):
        # Contract-level validation already happened via pydantic. Additionally
        # confine the audio path to the configured input root (raises a 422
        # unsafe_path if it escapes) and report engine availability — all
        # without running alignment or reading the audio bytes.
        resolve_input_path(cfg.input_root, request.audio_path)
        caps = registry.get(request.config.engine).capabilities()
        return {"valid": True, "engine": caps.to_dict(),
                "synthetic_opt_in_required": caps.is_synthetic and not request.config.allow_synthetic}

    @app.post("/alignments", status_code=201)
    def create_alignment(request: AlignmentRequestV1):
        return _public(store.create_and_process(request))

    @app.get("/alignments/{alignment_job_id}")
    def get_alignment(alignment_job_id: str):
        return _public(store.get(alignment_job_id))

    @app.post("/alignments/{alignment_job_id}/cancel")
    def cancel_alignment(alignment_job_id: str):
        return _public(store.cancel(alignment_job_id))

    @app.post("/alignments/{alignment_job_id}/retry")
    def retry_alignment(alignment_job_id: str):
        return _public(store.retry(alignment_job_id))

    @app.get("/alignments/{alignment_job_id}/artifact")
    def get_artifact(alignment_job_id: str):
        return store.get_artifact(alignment_job_id)

    @app.get("/alignments/{alignment_job_id}/export")
    def export_artifact(alignment_job_id: str):
        body = store.get_artifact(alignment_job_id)
        return JSONResponse(body, headers={
            "Content-Disposition": f'attachment; filename="alignment-{alignment_job_id}.json"',
            "Cache-Control": "no-store",
        })

    return app


def _public(row: dict) -> dict:
    """Project a DB job row into the API's status view (no internal request blob)."""
    return {
        "alignment_job_id": row["alignment_job_id"],
        "project_id": row["project_id"],
        "status": row["status"],
        "engine_name": row["engine_name"],
        "audio_sha256": row["audio_sha256"],
        "transcript_sha256": row["transcript_sha256"],
        "artifact_uri": row["artifact_uri"],
        "artifact_sha256": row["artifact_sha256"],
        "error_code": row["error_code"],
        "error_detail": row["error_detail"],
        "attempt_count": row["attempt_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


app = create_app()
