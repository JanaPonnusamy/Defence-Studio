"""FastAPI app. Minimal R1 API: renders, cancel, retry, output, QC, health. No UI."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from renderer import __version__, logging_conf
from renderer.config import Settings
from renderer.db import make_session_factory
from renderer.errors import RendererError
from renderer.jobs import JobService
from renderer.media import NOT_AVAILABLE, locate_tools
from renderer.upload import extract_package_zip

MAX_UPLOAD_BYTES = 2 * 1024 ** 3


class CreateRender(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_path: str
    idempotency_key: str | None = None
    options: dict | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        logging_conf.configure(settings.logs_dir)
        svc = JobService(settings, make_session_factory(settings.db_path))
        app.state.svc = svc
        app.state.recovery = svc.recover()
        yield
        svc.shutdown(wait=False)

    app = FastAPI(title="Standalone Video Renderer", version=__version__, lifespan=lifespan)

    @app.exception_handler(RendererError)
    async def _renderer_error(_: Request, exc: RendererError):
        body = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details:
            body["error"]["details"] = exc.details[:50]
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "request body is invalid",
                                       "details": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]}},
                            status_code=422)

    def svc() -> JobService:
        return app.state.svc

    @app.get("/health")
    def health():
        t = locate_tools(settings)
        return {"status": "ok", "version": __version__,
                "ffmpeg": {"status": "AVAILABLE", **t.info()} if t else {"status": NOT_AVAILABLE, "path": None,
                                                                            "version": NOT_AVAILABLE},
                "recovery": app.state.recovery, "storage_root": str(settings.storage_root)}

    @app.post("/renders", status_code=202)
    async def create_render(request: Request):
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            form = await request.form()
            up = form.get("file")
            if up is None or not hasattr(up, "read"):
                raise RendererError("INVALID_REQUEST", "multipart upload needs a 'file' field (package .zip)")
            key = form.get("idempotency_key") or None
            prior = svc().by_key(key) if key else None
            if prior is not None:  # idempotent upload: do not extract the same zip twice
                return JSONResponse({**prior.snapshot(), "created": False}, status_code=200)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=settings.storage_root) as tmp:
                size = 0
                while chunk := await up.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        Path(tmp.name).unlink(missing_ok=True)
                        raise RendererError("INVALID_REQUEST", "upload too large")
                    tmp.write(chunk)
            try:
                root = extract_package_zip(Path(tmp.name), settings.packages_dir)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
            try:
                job, created = svc().create(str(root), None, key)
            except RendererError:
                shutil.rmtree(root.parent if root.parent != settings.packages_dir else root, ignore_errors=True)
                raise
        else:
            try:
                body = CreateRender.model_validate(await request.json())
            except Exception:  # noqa: BLE001
                raise RendererError("INVALID_REQUEST", "expected JSON {package_path, idempotency_key?, options?} "
                                                       "or multipart with a 'file' zip") from None
            job, created = svc().create(body.package_path, body.options, body.idempotency_key)
        return JSONResponse({**job.snapshot(), "created": created}, status_code=202 if created else 200)

    @app.get("/renders")
    def list_renders(limit: int = 50, status: str | None = None):
        return {"renders": [j.snapshot() for j in svc().list(min(max(limit, 1), 200), status)]}

    @app.get("/renders/{job_id}")
    def get_render(job_id: str):
        return svc().get(job_id).snapshot()

    @app.post("/renders/{job_id}/cancel", status_code=202)
    def cancel_render(job_id: str):
        return svc().cancel(job_id).snapshot()

    @app.post("/renders/{job_id}/retry", status_code=202)
    def retry_render(job_id: str):
        return svc().retry(job_id).snapshot()

    @app.get("/renders/{job_id}/output")
    def get_output(job_id: str):
        job = svc().get(job_id)
        if job.status != "SUCCEEDED" or not job.output_path or not Path(job.output_path).is_file():
            raise RendererError("CONFLICT", f"no output: job is {job.status}")
        return FileResponse(job.output_path, media_type="video/mp4", filename=f"{job_id}.mp4")

    @app.get("/renders/{job_id}/qc")
    def get_qc(job_id: str):
        job = svc().get(job_id)
        qc = svc().job_dir(job_id) / "qc_report.json"
        if not qc.is_file():
            raise RendererError("CONFLICT", f"no QC report: job is {job.status}")
        return FileResponse(qc, media_type="application/json")

    return app


app = create_app()
