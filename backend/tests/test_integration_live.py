"""Opt-in live integration checks against REAL providers.

These are skipped by default. They perform real outbound network requests and
are the only place that proves a provider works end-to-end against a live
source; the mocked tests elsewhere prove behaviour, not reachability. yt-dlp
subtitle acquisition is free, so no paid API cost is incurred — but nothing here
runs without an explicit opt-in.

Enable with:
    DEFENCE_STUDIO_LIVE_HTTP=1       -> run the http_page live check
    DEFENCE_STUDIO_LIVE_YOUTUBE=1    -> run the youtube_subtitles live check
                                        (requires the 'youtube' extra installed)
"""

from __future__ import annotations

import os

import pytest

from defence_studio.acquisition.service import AcquisitionService
from defence_studio.contracts.acquisition import (
    AcquisitionRequestV1,
    AcquisitionTarget,
    SourceStatus,
)
from defence_studio.storage.artifacts import ArtifactStore

JOB = "99999999-9999-9999-9999-999999999999"


@pytest.mark.skipif(os.environ.get("DEFENCE_STUDIO_LIVE_HTTP") != "1",
                    reason="set DEFENCE_STUDIO_LIVE_HTTP=1 to run the live HTTP check")
def test_live_http_page(tmp_path):
    service = AcquisitionService(ArtifactStore(tmp_path / "storage"))
    url = os.environ.get("DEFENCE_STUDIO_LIVE_HTTP_URL", "https://example.com/")
    result = service.run(AcquisitionRequestV1(
        job_id=JOB, targets=(AcquisitionTarget(target_id="live", url=url),),
    ))
    source = result.sources[0]
    assert source.status == SourceStatus.ACQUIRED, source.error
    assert source.artifacts and source.artifacts[0].bytes > 0


@pytest.mark.skipif(os.environ.get("DEFENCE_STUDIO_LIVE_YOUTUBE") != "1",
                    reason="set DEFENCE_STUDIO_LIVE_YOUTUBE=1 to run the live YouTube check")
def test_live_youtube_subtitles(tmp_path):
    service = AcquisitionService(ArtifactStore(tmp_path / "storage"))
    url = os.environ.get("DEFENCE_STUDIO_LIVE_YOUTUBE_URL")
    if not url:
        pytest.skip("set DEFENCE_STUDIO_LIVE_YOUTUBE_URL to a video with captions")
    result = service.run(AcquisitionRequestV1(
        job_id=JOB,
        targets=(AcquisitionTarget(target_id="live", url=url,
                                   method="youtube_subtitles", language_preferences=("en",)),),
    ))
    source = result.sources[0]
    assert source.status == SourceStatus.ACQUIRED, source.error
    assert source.original_metadata.get("has_video_download") is False
