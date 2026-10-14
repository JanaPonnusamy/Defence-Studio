from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import audio, jobs, profiles, references, tts
from app.config import PROJECT_ROOT, get_settings
from app.db.init_db import init_db
from app.errors import AudioModuleError, audio_module_error_handler
from app.logging_conf import configure_logging, get_logger

configure_logging()
logger = get_logger("main")

app = FastAPI(
    title="Voice Audio Lab / Audio Module",
    description="Standalone reference ingestion, alignment, acoustic analysis, "
                 "performance-profile, and provider-neutral TTS module.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(AudioModuleError, audio_module_error_handler)

app.include_router(references.router)
app.include_router(jobs.router)
app.include_router(audio.router)
app.include_router(profiles.router)
app.include_router(tts.router)


@app.on_event("startup")
def on_startup() -> None:
    settings = get_settings()
    settings.ensure_storage_dirs()
    init_db()
    logger.info("Audio Module started. storage_root=%s db=%s", settings.storage_root, settings.database_url)


@app.get("/health")
def health():
    return {"status": "ok", "service": "audio-module"}


ui_dir = PROJECT_ROOT / "ui"
if ui_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
