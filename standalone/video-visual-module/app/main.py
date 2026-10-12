"""FastAPI application for the standalone Video Visual / Scene Composition Module.

Run:  .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
UI:   http://127.0.0.1:8765/ui/      API docs: http://127.0.0.1:8765/docs
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import assets, costs, jobs, projects, scenes, system, visual_plans
from app.api.deps import require_api_key
from app.config import MODULE_VERSION, PROJECT_ROOT, get_settings
from app.db.init_db import init_db
from app.errors import (
    VideoModuleError, http_exception_handler, unhandled_error_handler, validation_error_handler,
    video_module_error_handler,
)
from app.jobs import handlers  # noqa: F401  -- registers job handlers
from app.jobs.queue import recover_jobs, reset_job_queue
from app.logging_conf import configure_logging, get_logger

configure_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.ensure_storage_dirs()
    init_db()
    counts = recover_jobs()
    logger.info("Video Visual Module %s started. storage_root=%s recovery=%s", MODULE_VERSION, settings.storage_root, counts)
    yield
    reset_job_queue()
    logger.info("Video Visual Module stopped.")


app = FastAPI(
    title="Video Visual / Scene Composition Module",
    description="Standalone module: asset import with provenance/rights, narration-driven scene -> intent -> "
                "asset selection -> shot -> motion planning, provider-neutral AI image adapter with cost "
                "accounting, persisted jobs, and a renderer-neutral visual timeline (no video rendering).",
    version=MODULE_VERSION,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


app.add_exception_handler(VideoModuleError, video_module_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

_protected = [Depends(require_api_key)]
app.include_router(system.router)
for r in (projects.router, assets.router, scenes.router, visual_plans.router, jobs.router, costs.router):
    app.include_router(r, dependencies=_protected)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/")


_ui = PROJECT_ROOT / "ui"
if _ui.exists():
    app.mount("/ui", StaticFiles(directory=str(_ui), html=True), name="ui")
