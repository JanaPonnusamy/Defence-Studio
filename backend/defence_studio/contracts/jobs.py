from dataclasses import dataclass


JOB_REQUEST_SCHEMA = "defence-studio.job-create.v1"
JOB_STATE_SCHEMA = "defence-studio.job-state.v1"
ARTIFACT_REFERENCE_SCHEMA = "defence-studio.artifact-reference.v1"

JOB_STAGES = (
    "application_foundation",
    "news_feed_and_topic_discovery",
    "topic_search_and_source_collection",
    "extraction_validation_normalization_deduplication",
    "evidence_analysis_and_review",
    "script_generation_and_approval",
    "scene_creation_and_voice_direction",
    "asset_rights_review_and_assignment",
    "voice_audio_finishing_and_alignment",
    "video_rendering",
    "quality_control_final_review_and_export",
    "channel_branding_and_publishing_package",
    "cross_domain_and_format_reuse",
)

STAGE_STATUSES = frozenset(
    {"PENDING", "RUNNING", "AWAITING_REVIEW", "COMPLETED", "FAILED", "SKIPPED"}
)
JOB_STATUSES = frozenset(
    {"DRAFT", "SCHEDULED", "QUEUED", "AWAITING_REVIEW", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"}
)


@dataclass(frozen=True)
class JobCreateV1:
    topic: str
    language: str = "ta"


@dataclass(frozen=True)
class ArtifactReferenceV1:
    artifact_type: str
    stage_key: str
    version: int
    uri: str
    sha256: str
    schema_version: str


@dataclass(frozen=True)
class JobStateV1:
    job_id: str
    owner_id: str
    topic: str
    language: str
    status: str
    created_at: str
    updated_at: str
    stages: tuple[dict, ...]
    artifacts: tuple[dict, ...]
