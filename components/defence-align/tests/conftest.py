"""Shared offline test helpers. No network, no model downloads, no credentials.

Audio fixtures are generated at test time with the stdlib ``wave`` module, so
duration is real and the tests remain fully self-contained.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from align_engine._hashing import sha256_file
from align_engine.config import AlignConfig
from align_engine.contracts import AlignmentRequestV1, transcript_digest

EN_SECTIONS = [
    {"section_id": "s1", "segments": [
        {"segment_id": "s1-seg1", "original_text": "The DRDO's Rafale fleet reached Mach-2 in 2024."},
        {"segment_id": "s1-seg2", "original_text": "Officials said the AESA radar performed well."},
    ]},
]
TA_SECTIONS = [
    {"section_id": "t1", "segments": [
        {"segment_id": "t1-seg1", "original_text": "ராஃபேல் விமானம் DRDO ஆல் சோதிக்கப்பட்டது."},
        {"segment_id": "t1-seg2", "original_text": "AESA ரேடார் சிறப்பாக செயல்பட்டது."},
    ]},
]


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(seconds * rate))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)        # 16-bit PCM
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return path


def make_config(tmp_path: Path) -> AlignConfig:
    return AlignConfig(
        input_root=(tmp_path / "input").resolve(),
        storage_root=(tmp_path / "storage").resolve(),
        database_path=(tmp_path / "align.sqlite3").resolve(),
    )


def write_audio(config: AlignConfig, rel: str = "audio/sample.wav", seconds: float = 1.0) -> tuple[str, str]:
    abs_path = config.input_root / rel
    make_wav(abs_path, seconds=seconds)
    return rel, sha256_file(abs_path)


def build_request(config: AlignConfig, *, sections=None, language: str = "en",
                  engine: str = "deterministic-test", allow_synthetic: bool = True,
                  granularity: str = "word", job_id: str = "job-1",
                  audio_rel: str | None = None, audio_sha: str | None = None,
                  seconds: float = 1.0) -> AlignmentRequestV1:
    if audio_rel is None:
        audio_rel, audio_sha = write_audio(config, seconds=seconds)
    sections = sections if sections is not None else EN_SECTIONS
    draft = AlignmentRequestV1(
        schema_version="defence-align.request.v1", alignment_job_id=job_id,
        project_id="proj-1", audio_artifact_id="aud-1", audio_path=audio_rel,
        audio_sha256=audio_sha, transcript_id="tr-1", transcript_sha256="0" * 64,
        language=language, sections=sections,
        config={"engine": engine, "granularity": granularity, "allow_synthetic": allow_synthetic},
    )
    return draft.model_copy(update={"transcript_sha256": transcript_digest(draft)})


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)
