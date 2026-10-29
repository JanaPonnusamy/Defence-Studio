"""Research entry accepts only the supported languages and rejects anything else before writing.

Job creation and discovery-request bodies used to accept any 2-20 character
string, so a typo such as "EN" or "tamil" created a job the pipeline cannot
produce. They now accept exactly SUPPORTED_LANGUAGES and answer 422 otherwise.
"""
import uuid

import pytest

from defence_studio.contracts.discovery import SUPPORTED_LANGUAGES

INVALID = ["fr", "EN", "Ta", " en", "en ", "en-US", "ta-IN", "tamil", "", "e", None, 1, ["en"], "<script>"]


def _create(client, headers, **body):
    return client.post("/api/jobs", json={"topic": "language check", **body},
                       headers={**headers, "Idempotency-Key": str(uuid.uuid4())})


def _language_error(response):
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert isinstance(detail, list) and any(item["loc"][-1] == "language" for item in detail), detail
    return detail


def test_supported_languages_match_the_pipeline_contracts():
    from defence_studio.contracts.scene import LANGUAGES as SCENE
    from defence_studio.contracts.script import LANGUAGES as SCRIPT
    assert set(SUPPORTED_LANGUAGES) == set(SCRIPT) == set(SCENE) == {"en", "ta"}


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_supported_languages_create_jobs_unchanged(client, auth_headers, language):
    response = _create(client, auth_headers, language=language)
    assert response.status_code == 201, response.text
    assert response.json()["language"] == language


def test_missing_language_keeps_the_existing_default(client, auth_headers):
    response = _create(client, auth_headers)
    assert response.status_code == 201 and response.json()["language"] == "ta"


@pytest.mark.parametrize("language", INVALID)
def test_unsupported_or_malformed_language_creates_no_job(client, auth_headers, language):
    _language_error(_create(client, auth_headers, language=language))
    assert client.get("/api/jobs", headers=auth_headers).json()["jobs"] == []


@pytest.mark.parametrize("language", INVALID)
def test_discovery_request_with_bad_language_is_rejected_and_job_untouched(client, auth_headers, language):
    job = _create(client, auth_headers, language="en").json()
    response = client.post(f"/api/jobs/{job['job_id']}/discovery/requests", headers=auth_headers,
                           json={"topic": "language check", "language": language})
    _language_error(response)
    after = client.get(f"/api/jobs/{job['job_id']}", headers=auth_headers).json()
    assert after["status"] == "DRAFT" and after["language"] == "en" and after["updated_at"] == job["updated_at"]
    assert client.get(f"/api/jobs/{job['job_id']}/discovery/requests", headers=auth_headers).json()["request"] is None


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_discovery_request_with_supported_language_is_saved(client, auth_headers, language):
    job = _create(client, auth_headers, language=language).json()
    response = client.post(f"/api/jobs/{job['job_id']}/discovery/requests", headers=auth_headers,
                           json={"topic": "language check", "language": language})
    assert response.status_code == 201, response.text
    assert response.json()["request"]["language"] == language
