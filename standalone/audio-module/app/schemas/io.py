from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ReferenceOut(BaseModel):
    reference_id: str
    source_type: str
    source_location: str | None = None
    title: str | None = None
    duration: float | None = None
    language: str
    audio_id: str | None = None
    transcript_id: str | None = None
    alignment_id: str | None = None
    analysis_id: str | None = None
    created_at: datetime
    status: str
    rights_metadata: dict

    class Config:
        from_attributes = True


class JobOut(BaseModel):
    job_id: str
    job_type: str
    status: str
    progress: float
    stage: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_id: str | None = None

    class Config:
        from_attributes = True


class ProfileOut(BaseModel):
    profile_id: str
    profile_name: str
    source_analysis_ids: list
    tempo: str | None
    pause_style: str | None
    energy: str | None
    emphasis: str | None
    pitch_style: str | None
    transition_pause: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class BuildProfileRequest(BaseModel):
    profile_name: str
    analysis_ids: list[str]


class TTSSynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    voice_id: str | None = None
    language: str = "en"
    performance_plan_id: str | None = None
    provider: str | None = None
    output_format: str = "wav"
    controls: dict = Field(default_factory=dict)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Text cannot be blank')
        return value

    @field_validator('controls')
    @classmethod
    def supported_controls(cls, value):
        from app.tts.controls import resolve_controls
        resolve_controls(value)
        return value


class GeneratedAudioOut(BaseModel):
    generated_id: str
    job_id: str | None
    text: str
    voice_id: str | None
    language: str
    performance_plan_id: str | None
    provider: str
    model: str | None
    output_format: str
    duration: float | None
    sample_rate: int | None
    channels: int | None
    validation_status: str | None
    validation_details: dict
    provider_metadata: dict
    created_at: datetime

    class Config:
        from_attributes = True


class PerformancePlanRequest(BaseModel):
    script: str
    profile_id: str
    language: str = "en"
