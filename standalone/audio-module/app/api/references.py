from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import get_session
from app.db.models import AudioAsset, Job, Reference, Transcript
from app.errors import NotFoundError, ValidationFailedError
from app.jobs.handlers import process_reference_job
from app.jobs.queue import get_job_queue
from app.pipeline.ingest import ingest_upload
from app.schemas.io import JobOut, ReferenceOut

router = APIRouter(prefix="/references", tags=["references"])


@router.post("", response_model=ReferenceOut)
def create_reference(
    title: str = Form(...),
    language: str = Form("en"),
    rights_metadata: str = Form("{}"),
    audio_file: UploadFile = File(...),
    transcript_text: str | None = Form(None),
    transcript_format: str = Form("plain"),
    db: Session = Depends(get_session),
):
    try:
        rights = json.loads(rights_metadata) if rights_metadata else {}
    except json.JSONDecodeError:
        raise ValidationFailedError("rights_metadata must be valid JSON")

    ref = Reference(source_type="upload", title=title, language=language, rights_metadata=rights, status="REGISTERED")
    db.add(ref)
    db.commit()

    settings = get_settings()
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(audio_file.filename).suffix) as tmp:
        shutil.copyfileobj(audio_file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        stored_path = ingest_upload(ref.reference_id, tmp_path, audio_file.filename)
    finally:
        tmp_path.unlink(missing_ok=True)

    asset = AudioAsset(reference_id=ref.reference_id, file_path=str(stored_path), original_filename=audio_file.filename)
    db.add(asset)
    db.commit()
    ref.audio_id = asset.audio_id

    if transcript_text:
        transcript = Transcript(reference_id=ref.reference_id, source_format=transcript_format, raw_text=transcript_text)
        transcript_file = settings.transcripts_dir / f"{ref.reference_id}.txt"
        transcript_file.write_text(transcript_text, encoding="utf-8")
        transcript.file_path = str(transcript_file)
        db.add(transcript)
        db.commit()
        ref.transcript_id = transcript.transcript_id

    ref.status = "INGESTED"
    db.commit()
    db.refresh(ref)
    return ref


@router.get("", response_model=list[ReferenceOut])
def list_references(db: Session = Depends(get_session)):
    return db.query(Reference).order_by(Reference.created_at.desc()).all()


@router.get("/{reference_id}", response_model=ReferenceOut)
def get_reference(reference_id: str, db: Session = Depends(get_session)):
    ref = db.get(Reference, reference_id)
    if not ref:
        raise NotFoundError(f"Reference {reference_id} not found")
    return ref


@router.delete("/{reference_id}")
def delete_reference(reference_id: str, db: Session = Depends(get_session)):
    ref = db.get(Reference, reference_id)
    if not ref:
        raise NotFoundError(f"Reference {reference_id} not found")
    settings = get_settings()
    ref_dir = settings.audio_dir / reference_id
    if ref_dir.exists():
        shutil.rmtree(ref_dir, ignore_errors=True)
    if ref.audio_id:
        asset = db.get(AudioAsset, ref.audio_id)
        if asset:
            db.delete(asset)
    db.delete(ref)
    db.commit()
    return {"deleted": True, "reference_id": reference_id}


@router.post("/{reference_id}/process", response_model=JobOut)
def process_reference(reference_id: str, db: Session = Depends(get_session)):
    ref = db.get(Reference, reference_id)
    if not ref:
        raise NotFoundError(f"Reference {reference_id} not found")

    job = Job(job_type="process_reference", status="QUEUED", payload={"reference_id": reference_id})
    db.add(job)
    db.commit()
    db.refresh(job)

    get_job_queue().submit(job.job_id, process_reference_job, job.payload)
    return job
