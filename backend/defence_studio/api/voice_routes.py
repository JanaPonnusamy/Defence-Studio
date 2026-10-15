"""HTTP routes for the Voice Analyzer / Voice Generation integration.

Owner-authenticated, schema-validated, no client-controlled file paths.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from defence_studio.services.voice import VoiceService

_EXTENSIONS = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".aac"}


class GenerationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: str = Field(min_length=1, max_length=20000)
    direction: dict


def register_voice_routes(app: FastAPI, require_owner, voice: VoiceService) -> None:
    idem_header = Header(min_length=1, max_length=200, alias="Idempotency-Key")

    @app.get("/api/voice/providers")
    def providers(owner_id: str = Depends(require_owner)):
        return voice.providers()

    # ---- analyzer ---------------------------------------------------------
    @app.post("/api/voice/analysis", status_code=202)
    async def start_analysis(
        file: UploadFile = File(...),
        profile_name: str = Form(..., min_length=1, max_length=200),
        language: str = Form(..., min_length=2, max_length=20),
        authorization_ref: str = Form(..., min_length=1, max_length=500),
        provenance_state: str = Form("AUTHORIZED"),
        transcript: str | None = Form(None, max_length=20000),
        base_voice_style_id: str | None = Form(None, max_length=100),
        idempotency_key: str = idem_header,
        owner_id: str = Depends(require_owner),
    ):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in _EXTENSIONS:
            raise HTTPException(422, f"Unsupported file type {ext or '(none)'}; allowed: {', '.join(sorted(_EXTENSIONS))}")
        dest = voice.upload_dir / f"{uuid.uuid4().hex}{ext}"   # server-chosen name only
        digest, size = hashlib.sha256(), 0
        try:
            with open(dest, "wb") as out:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > voice.max_upload_bytes:
                        raise HTTPException(413, f"File exceeds {voice.max_upload_bytes} bytes")
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "Uploaded file is empty")
            return voice.create_analysis(
                owner_id, idempotency_key, temp_path=dest, original_name=file.filename or "upload", size_bytes=size,
                sha256=digest.hexdigest(), profile_name=profile_name, language=language,
                authorization_ref=authorization_ref, provenance_state=provenance_state,
                transcript=transcript, base_voice_style_id=base_voice_style_id)
        except Exception:
            # no job owns this file if creation failed; never leave orphaned reference audio
            from defence_studio.database import connect
            from contextlib import closing
            with closing(connect(voice.database_file)) as c:
                owned = c.execute("SELECT 1 FROM voice_job_requests WHERE temp_path=?", (str(dest),)).fetchone()
            if not owned:
                dest.unlink(missing_ok=True)
            raise

    @app.get("/api/voice/profiles")
    def list_profiles(owner_id: str = Depends(require_owner)):
        return {"profiles": voice.list_profiles(owner_id)}

    @app.get("/api/voice/profiles/{voice_style_id}")
    def get_profile(voice_style_id: str, version: int | None = None, owner_id: str = Depends(require_owner)):
        return voice.profile(voice_style_id, owner_id, version)

    @app.get("/api/voice/profiles/{voice_style_id}/segments")
    def get_segments(voice_style_id: str, version: int | None = None, owner_id: str = Depends(require_owner)):
        return voice.profile_segments(voice_style_id, owner_id, version)

    @app.get("/api/voice/profiles/{voice_style_id}/compare")
    def compare(voice_style_id: str, a: int, b: int, owner_id: str = Depends(require_owner)):
        return voice.compare(voice_style_id, owner_id, a, b)

    # ---- generation -------------------------------------------------------
    @app.post("/api/voice/generations", status_code=202)
    def start_generation(body: GenerationBody, idempotency_key: str = idem_header,
                         owner_id: str = Depends(require_owner)):
        return voice.create_generation(owner_id, idempotency_key, script=body.script, direction=body.direction)

    @app.get("/api/voice/generations/{generation_id}")
    def get_generation(generation_id: str, owner_id: str = Depends(require_owner)):
        return voice.generation(generation_id, owner_id)

    @app.get("/api/voice/generations/{generation_id}/audio")
    def get_audio(generation_id: str, download: bool = False, owner_id: str = Depends(require_owner)):
        path = voice.generation_audio_path(generation_id, owner_id)
        return FileResponse(path, media_type="audio/wav",
                            filename=f"{generation_id}.wav" if download else None,
                            headers={"Cache-Control": "private, max-age=0"})

    @app.get("/api/voice/generations/{generation_id}/alignment")
    def get_alignment(generation_id: str, owner_id: str = Depends(require_owner)):
        return voice.alignment(generation_id, owner_id)

    # ---- jobs / history ---------------------------------------------------
    @app.get("/api/voice/jobs")
    def list_jobs(kind: str | None = None, owner_id: str = Depends(require_owner)):
        if kind not in (None, "ANALYSIS", "GENERATION"):
            raise HTTPException(422, "kind must be ANALYSIS or GENERATION")
        return {"jobs": voice.list_jobs(owner_id, kind)}

    @app.get("/api/voice/jobs/{job_id}")
    def get_job(job_id: str, owner_id: str = Depends(require_owner)):
        return voice.get_job(job_id, owner_id)

    @app.post("/api/voice/jobs/{job_id}/cancel")
    def cancel(job_id: str, owner_id: str = Depends(require_owner)):
        return voice.cancel(job_id, owner_id)

    @app.post("/api/voice/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str, idempotency_key: str = idem_header, owner_id: str = Depends(require_owner)):
        return voice.retry(job_id, owner_id, idempotency_key)

    @app.delete("/api/voice/jobs/{job_id}/reference")
    def discard_reference(job_id: str, owner_id: str = Depends(require_owner)):
        return voice.discard_reference(job_id, owner_id)
