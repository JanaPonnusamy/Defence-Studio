from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.models import AudioAsset, Alignment, Analysis, GeneratedAudio, Transcript
from app.errors import NotFoundError

router = APIRouter(tags=["assets"])


@router.get("/audio/{audio_id}")
def get_audio_metadata(audio_id: str, db: Session = Depends(get_session)):
    asset = db.get(AudioAsset, audio_id)
    if not asset:
        raise NotFoundError(f"Audio {audio_id} not found")
    return {
        "audio_id": asset.audio_id, "reference_id": asset.reference_id,
        "sample_rate": asset.sample_rate, "channels": asset.channels, "bit_depth": asset.bit_depth,
        "duration": asset.duration, "loudness_estimate_dbfs": asset.loudness_lufs, "peak_dbfs": asset.peak_dbfs,
        "format": asset.format, "checksum_sha256": asset.checksum_sha256, "is_normalized": asset.is_normalized,
        "waveform_url": f"/audio/{audio_id}/waveform" if asset.waveform_path else None,
        "stream_url": f"/audio/{audio_id}/file",
    }


@router.get("/audio/{audio_id}/file")
def get_audio_file(audio_id: str, db: Session = Depends(get_session)):
    asset = db.get(AudioAsset, audio_id)
    if not asset:
        raise NotFoundError(f"Audio {audio_id} not found")
    normalized = Path(asset.file_path).parent / "normalized.wav"
    path = normalized if normalized.exists() else Path(asset.file_path)
    return FileResponse(str(path), media_type="audio/wav")


@router.get("/audio/{audio_id}/waveform")
def get_audio_waveform(audio_id: str, db: Session = Depends(get_session)):
    asset = db.get(AudioAsset, audio_id)
    if not asset or not asset.waveform_path:
        raise NotFoundError(f"Waveform for audio {audio_id} not found")
    return json.loads(Path(asset.waveform_path).read_text(encoding="utf-8"))


@router.get("/transcripts/{transcript_id}")
def get_transcript(transcript_id: str, db: Session = Depends(get_session)):
    t = db.get(Transcript, transcript_id)
    if not t:
        raise NotFoundError(f"Transcript {transcript_id} not found")
    return {"transcript_id": t.transcript_id, "source_format": t.source_format, "raw_text": t.raw_text}


@router.get("/alignment/{alignment_id}")
def get_alignment(alignment_id: str, db: Session = Depends(get_session)):
    a = db.get(Alignment, alignment_id)
    if not a:
        raise NotFoundError(f"Alignment {alignment_id} not found")
    data = json.loads(Path(a.file_path).read_text(encoding="utf-8")) if a.file_path else {}
    return {
        "alignment_id": a.alignment_id, "provider": a.provider, "quality_label": a.quality_label,
        "mean_confidence": a.mean_confidence, **data,
    }


@router.get("/analysis/{analysis_id}")
def get_analysis(analysis_id: str, db: Session = Depends(get_session)):
    a = db.get(Analysis, analysis_id)
    if not a:
        raise NotFoundError(f"Analysis {analysis_id} not found")
    data = json.loads(Path(a.file_path).read_text(encoding="utf-8")) if a.file_path else {}
    return {"analysis_id": a.analysis_id, **data}


@router.get("/generated/{generated_id}")
def get_generated(generated_id: str, db: Session = Depends(get_session)):
    g = db.get(GeneratedAudio, generated_id)
    if not g:
        raise NotFoundError(f"Generated audio {generated_id} not found")
    return {
        "generated_id": g.generated_id, "text": g.text, "provider": g.provider, "model": g.model,
        "duration": g.duration, "sample_rate": g.sample_rate, "channels": g.channels,
        "validation_status": g.validation_status, "validation_details": g.validation_details,
        "provider_metadata": g.provider_metadata, "stream_url": f"/generated/{generated_id}/file",
    }


@router.get("/generated/{generated_id}/file")
def get_generated_file(generated_id: str, db: Session = Depends(get_session)):
    g = db.get(GeneratedAudio, generated_id)
    if not g or not g.file_path:
        raise NotFoundError(f"Generated audio {generated_id} not found")
    return FileResponse(g.file_path, media_type="audio/wav")
