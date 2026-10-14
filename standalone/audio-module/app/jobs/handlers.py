"""Job handler functions: the actual pipeline orchestration invoked by the
job queue. Each handler receives (job_id, ctx) and does real work against
this module's own database and storage -- no mocked results anywhere
outside the explicitly-labeled mock TTS adapter.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import librosa
import soundfile as sf

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import AudioAsset, Alignment, Analysis, GeneratedAudio, Profile, Reference, Transcript
from app.jobs.queue import JobContext
from app.logging_conf import get_logger
from app.pipeline.align import align_vad_proportional
from app.pipeline.analyze import analyze_audio
from app.pipeline.normalize import normalize_audio
from app.pipeline.profile import build_profile
from app.pipeline.transcript import load_transcript
from app.pipeline.validate import validate_generated_audio
from app.pipeline.waveform import write_waveform_json
from app.tts.base import TTSRequest
from app.tts.registry import get_adapter

logger = get_logger("jobs.handlers")


def process_reference_job(job_id: str, ctx: JobContext) -> str:
    """Full pipeline: NORMALIZE -> LOAD TRANSCRIPT -> ALIGN -> ANALYZE ->
    STORE RESULTS, for a previously-registered reference with an uploaded
    original audio file."""
    settings = get_settings()
    db = SessionLocal()
    try:
        from app.db.models import Job
        job = db.get(Job, job_id)
        reference_id = job.payload["reference_id"]
        ref = db.get(Reference, reference_id)
        if ref is None:
            raise ValueError(f"Reference {reference_id} not found")

        ctx.update(progress=0.05, stage="NORMALIZE")
        audio_asset = db.query(AudioAsset).filter_by(audio_id=ref.audio_id).first()
        original_path = Path(audio_asset.file_path)
        normalized_path = settings.audio_dir / reference_id / "normalized.wav"
        norm_result = normalize_audio(original_path, normalized_path)

        audio_asset.sample_rate = norm_result.sample_rate
        audio_asset.channels = norm_result.channels
        audio_asset.bit_depth = norm_result.bit_depth
        audio_asset.duration = norm_result.duration
        audio_asset.loudness_lufs = norm_result.loudness_lufs
        audio_asset.peak_dbfs = norm_result.peak_dbfs
        audio_asset.format = norm_result.format
        audio_asset.checksum_sha256 = norm_result.checksum_sha256
        audio_asset.is_normalized = True
        ref.duration = norm_result.duration
        ref.status = "NORMALIZED"
        db.commit()

        ctx.update(progress=0.25, stage="WAVEFORM")
        y, sr = librosa.load(str(normalized_path), sr=None, mono=True)
        waveform_path = settings.waveforms_dir / f"{audio_asset.audio_id}.json"
        write_waveform_json(y, sr, waveform_path)
        audio_asset.waveform_path = str(waveform_path)
        db.commit()

        ctx.update(progress=0.4, stage="LOAD_TRANSCRIPT")
        transcript = db.query(Transcript).filter_by(transcript_id=ref.transcript_id).first() if ref.transcript_id else None
        if transcript is None:
            ref.status = "ANALYZED_NO_TRANSCRIPT"
            db.commit()
            ctx.update(progress=1.0, stage="DONE_NO_TRANSCRIPT")
            return ref.reference_id

        segments = load_transcript(transcript.raw_text, transcript.source_format)

        ctx.update(progress=0.55, stage="ALIGN")
        aligned, mean_conf, quality_label = align_vad_proportional(y, sr, segments)

        alignment_payload = {
            "segments": [
                {
                    "text": a.text, "start": a.start, "end": a.end, "confidence": a.confidence,
                    "words": [asdict(w) for w in a.words],
                } for a in aligned
            ],
            "quality_label": quality_label,
            "mean_confidence": mean_conf,
            "provider": settings.alignment_provider,
        }
        alignment_file = settings.alignment_dir / f"{reference_id}.json"
        alignment_file.write_text(json.dumps(alignment_payload, indent=2), encoding="utf-8")

        alignment = Alignment(
            reference_id=reference_id, audio_id=audio_asset.audio_id, transcript_id=transcript.transcript_id,
            provider=settings.alignment_provider, quality_label=quality_label, mean_confidence=mean_conf,
            file_path=str(alignment_file),
        )
        db.add(alignment)
        db.flush()  # populate alignment.alignment_id (Python-side default) before it is referenced
        ref.alignment_id = alignment.alignment_id
        db.commit()

        ctx.update(progress=0.75, stage="ANALYZE")
        analysis_result = analyze_audio(y, sr, aligned)
        analysis_dict = asdict(analysis_result)
        analysis_file = settings.analysis_dir / f"{reference_id}.json"
        analysis_file.write_text(json.dumps(analysis_dict, indent=2, default=str), encoding="utf-8")

        analysis = Analysis(
            reference_id=reference_id, audio_id=audio_asset.audio_id, alignment_id=alignment.alignment_id,
            file_path=str(analysis_file),
        )
        db.add(analysis)
        db.flush()  # populate analysis.analysis_id before it is referenced
        ref.analysis_id = analysis.analysis_id
        ref.status = "ANALYZED"
        db.commit()

        ctx.update(progress=1.0, stage="DONE")
        return ref.reference_id
    finally:
        db.close()


def build_profile_job(job_id: str, ctx: JobContext) -> str:
    settings = get_settings()
    db = SessionLocal()
    try:
        from app.db.models import Job
        job = db.get(Job, job_id)
        profile_name = job.payload["profile_name"]
        analysis_ids = job.payload["analysis_ids"]

        ctx.update(progress=0.2, stage="LOADING_ANALYSES")
        analyses = []
        for aid in analysis_ids:
            analysis_row = db.get(Analysis, aid)
            if analysis_row is None:
                raise ValueError(f"Analysis {aid} not found")
            analyses.append(json.loads(Path(analysis_row.file_path).read_text(encoding="utf-8")))

        ctx.update(progress=0.6, stage="BUILDING_PROFILE")
        result = build_profile(profile_name, analyses)

        profile_file = settings.profiles_dir / f"{profile_name.replace(' ', '_')}_{job_id}.json"
        profile_file.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")

        profile = Profile(
            profile_name=result.profile_name, source_analysis_ids=analysis_ids, file_path=str(profile_file),
            tempo=result.tempo, pause_style=result.pause_style, energy=result.energy,
            emphasis=result.emphasis, pitch_style=result.pitch_style, transition_pause=result.transition_pause,
        )
        db.add(profile)
        db.commit()

        ctx.update(progress=1.0, stage="DONE")
        return profile.profile_id
    finally:
        db.close()


def tts_synthesize_job(job_id: str, ctx: JobContext) -> str:
    settings = get_settings()
    db = SessionLocal()
    try:
        from app.db.models import Job
        job = db.get(Job, job_id)
        payload = job.payload
        provider = payload.get("provider")
        text = payload["text"]
        voice_id = payload.get("voice_id")
        language = payload.get("language", "en")
        performance_plan = payload.get("performance_plan", [])
        output_format = payload.get("output_format", "wav")

        ctx.update(progress=0.2, stage="SYNTHESIZING")
        adapter = get_adapter(provider)
        dest_path = settings.generated_dir / f"{job_id}.wav"
        request = TTSRequest(text=text, voice_id=voice_id, language=language,
                              performance_plan=performance_plan, output_format=output_format)
        result = adapter.synthesize(request, dest_path)

        ctx.update(progress=0.7, stage="VALIDATING")
        validation = validate_generated_audio(result.result_path, expected_language=language,
                                               provider_metadata=result.provider_metadata)

        y, sr = sf.read(str(result.result_path))
        duration = len(y) / sr if hasattr(y, "__len__") else 0.0
        channels = 1 if (not hasattr(y, "ndim") or y.ndim == 1) else y.shape[1]

        generated = GeneratedAudio(
            job_id=job_id, text=text, voice_id=voice_id, language=language,
            performance_plan_id=payload.get("performance_plan_id"), provider=result.provider,
            model=result.model, output_format=result.output_format, file_path=str(result.result_path),
            duration=duration, sample_rate=sr, channels=channels,
            validation_status=validation["status"], validation_details=validation["details"],
            provider_metadata={**result.provider_metadata, "is_mock": adapter.is_mock, "voice": result.voice},
        )
        db.add(generated)
        db.commit()

        ctx.update(progress=1.0, stage="DONE")
        return generated.generated_id
    finally:
        db.close()
