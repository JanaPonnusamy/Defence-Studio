"""Contract validation: schema, language, IDs, hashes, transcript digest."""
import pytest
from pydantic import ValidationError

from align_engine.contracts import AlignmentRequestV1, transcript_digest
from conftest import EN_SECTIONS, build_request, make_config


def _base(**overrides):
    body = dict(
        schema_version="defence-align.request.v1", alignment_job_id="j1", project_id="p1",
        audio_artifact_id="a1", audio_path="audio/x.wav", audio_sha256="a" * 64,
        transcript_id="t1", transcript_sha256="b" * 64, language="en", sections=EN_SECTIONS,
    )
    body.update(overrides)
    return body


def test_valid_request_parses():
    req = AlignmentRequestV1.model_validate(_base())
    assert req.language == "en" and req.config.engine == "deterministic-test"


def test_reject_unknown_field():
    with pytest.raises(ValidationError):
        AlignmentRequestV1.model_validate(_base(unexpected="x"))


def test_reject_unsupported_schema_version():
    with pytest.raises(ValidationError):
        AlignmentRequestV1.model_validate(_base(schema_version="defence-align.request.v999"))


def test_reject_invalid_language():
    with pytest.raises(ValidationError):
        AlignmentRequestV1.model_validate(_base(language="fr"))


def test_reject_empty_transcript():
    with pytest.raises(ValidationError):
        AlignmentRequestV1.model_validate(_base(sections=[]))


def test_reject_bad_sha():
    with pytest.raises(ValidationError):
        AlignmentRequestV1.model_validate(_base(audio_sha256="not-a-hash"))


def test_reject_duplicate_section_ids():
    dup = [EN_SECTIONS[0], {"section_id": "s1", "segments": [{"segment_id": "zz", "original_text": "hi"}]}]
    with pytest.raises(ValidationError, match="Duplicate section_id"):
        AlignmentRequestV1.model_validate(_base(sections=dup))


def test_reject_duplicate_segment_ids():
    dup = [{"section_id": "s1", "segments": [
        {"segment_id": "d", "original_text": "a"}, {"segment_id": "d", "original_text": "b"}]}]
    with pytest.raises(ValidationError, match="Duplicate segment_id"):
        AlignmentRequestV1.model_validate(_base(sections=dup))


def test_normalized_text_is_separate_optional_metadata():
    body = _base(sections=[{"section_id": "s", "segments": [
        {"segment_id": "g", "original_text": "AESA radar", "normalized_text": "aesa radar"}]}])
    req = AlignmentRequestV1.model_validate(body)
    seg = req.sections[0].segments[0]
    assert seg.original_text == "AESA radar" and seg.normalized_text == "aesa radar"


def test_transcript_digest_is_deterministic_and_content_bound(tmp_path):
    config = make_config(tmp_path)
    r1 = build_request(config, job_id="a")
    r2 = build_request(config, job_id="b")  # different job id, same transcript
    assert transcript_digest(r1) == transcript_digest(r2)
    assert r1.transcript_sha256 == transcript_digest(r1)
