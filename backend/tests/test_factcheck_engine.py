"""Fact-check and media-context engines. Fixtures are synthetic."""

import json
import struct
import zlib
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.database import SCHEMA_VERSION, connect, initialize_database
from defence_studio.services.factcheck import (
    BudgetExceeded, FactcheckService, PermanentVerificationError, authorize_paid, script_gate,
)
from defence_studio.services.jobs import JobService
from defence_studio.verification.claims import normalize_text, verify_claims
from defence_studio.verification.media import average_hash, hamming, rights_status, sample_video_frames, sha256, verify_media
from defence_studio.verification.package import build_evidence_package, usable_claim_ids
from defence_studio.verification.providers import select_provider
from conftest import SECRET, access_token

OWNER = "owner-factcheck"


def _png(width, height, paint, texts=None):
    def chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    raw = b""
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            raw += bytes(paint(x, y))
    chunks = [chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))]
    for key, value in (texts or {}).items():
        chunks.append(chunk(b"tEXt", key.encode("latin1") + b"\x00" + value.encode("latin1")))
    chunks.append(chunk(b"IDAT", zlib.compress(raw)))
    chunks.append(chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _ppm(width, height, paint):
    body = bytearray()
    for y in range(height):
        for x in range(width):
            body += bytes(paint(x, y))
    return f"P6\n{width} {height}\n255\n".encode() + bytes(body)


def _source(source_id, excerpt, **extra):
    base = {
        "source_id": source_id, "publisher": source_id, "url": f"https://example.test/{source_id}",
        "category": "news", "published_at": "2024-03-02T00:00:00+00:00", "named_attribution": True,
        "primary": True, "excerpt": excerpt,
    }
    base.update(extra)
    return base


def _claim(text, **extra):
    base = {
        "claim_id": extra.pop("claim_id", "c1"), "text": text, "source_id": "origin",
        "category": "equipment", "published_at": "2024-03-01T00:00:00+00:00",
        "event_date": "2024-03-01", "entities": ["Northwind"], "extraction_confidence": 0.95,
        "extraction_method": "rule",
    }
    base.update(extra)
    return base


STATEMENT = "Northwind base counted 4 patrol craft during the March drill."
SUPPORT_A = "Northwind base counted 4 patrol craft during the March drill according to the harbour master."
SUPPORT_B = "Northwind base counted 4 patrol craft during the March drill as recorded by the quay observers."


def test_normalization_keeps_hedges_and_collapses_space():
    assert normalize_text("  Reportedly   not   confirmed  ") == "Reportedly not confirmed"


def test_duplicate_text_clusters_and_syndication_is_one_group():
    copied = "Northwind base counted 4 patrol craft during the March drill."
    result = verify_claims(
        [_claim(STATEMENT, claim_id="c1"), _claim(copied, claim_id="c2", source_id="other")],
        [
            _source("origin", STATEMENT),
            _source("wire-a", copied, publisher="Wire A"),
            _source("wire-b", copied, publisher="Wire B"),
        ],
    )
    assert result["verdicts"][0]["cluster_id"] == result["verdicts"][1]["cluster_id"]
    assert len(result["verdicts"][0]["independent_support_groups"]) == 1
    assert result["verdicts"][0]["verdict"] == "PARTIALLY_SUPPORTED"


def test_verified_needs_two_independent_supports():
    result = verify_claims(
        [_claim(STATEMENT)],
        [_source("origin", STATEMENT),
         _source("desk-a", SUPPORT_A, publisher="Desk A", retrieved_at="2024-03-02T12:00:00+00:00"),
         _source("desk-b", SUPPORT_B, publisher="Desk B", retrieved_at="2024-03-02T12:05:00+00:00")],
    )
    verdict = result["verdicts"][0]
    assert verdict["verdict"] == "VERIFIED"
    assert verdict["falsity_assessment"] == "NONE"
    assert len(verdict["independent_support_groups"]) == 2
    assert verdict["confidence"]["calibrated"] is False
    urls = {row["url"] for row in verdict["supporting"]}
    assert urls == {"https://example.test/desk-a", "https://example.test/desk-b"}
    assert all(row["published_at"] for row in verdict["supporting"])
    assert all(row["retrieved_at"] for row in verdict["supporting"])
    assert all(row["retrieved_at_status"] == "COLLECTED" for row in verdict["supporting"])
    assert all(row["independence_group"] for row in verdict["supporting"])


def test_conflict_and_outdated_and_unsupported_stay_distinct():
    conflicting = verify_claims(
        [_claim(STATEMENT)],
        [_source("origin", STATEMENT), _source("desk-a", SUPPORT_A),
         _source("desk-b", "Northwind base counted 9 patrol craft during the March drill.", publisher="Desk B")],
    )
    assert conflicting["verdicts"][0]["verdict"] == "CONFLICTING"
    outdated = verify_claims(
        [_claim(STATEMENT, published_at="2020-01-01T00:00:00+00:00")],
        [_source("origin", STATEMENT, published_at="2020-01-01T00:00:00+00:00"),
         _source("later", "Northwind base counted 9 patrol craft during the March drill.",
                 published_at="2024-06-01T00:00:00+00:00")],
    )
    assert outdated["verdicts"][0]["verdict"] == "OUTDATED"
    unsupported = verify_claims(
        [_claim(STATEMENT)],
        [_source("origin", STATEMENT), _source("other", "The harbour cafe opened a new window.")],
        collection={"attempted": True, "errors": []},
    )
    assert unsupported["verdicts"][0]["verdict"] == "UNSUPPORTED"
    assert unsupported["verdicts"][0]["falsity_assessment"] == "NONE"
    missing = verify_claims([_claim(STATEMENT)], [_source("origin", STATEMENT)], collection={"attempted": False})
    assert missing["verdicts"][0]["verdict"] == "NEEDS_REVIEW"


def test_high_impact_and_debunked_are_separate_from_unsupported():
    held = verify_claims(
        [_claim("Northwind reported 4 casualties after the drill.", category="casualty", entities=["Northwind"])],
        [_source("origin", "Northwind reported 4 casualties after the drill."),
         _source("desk-a", "Northwind reported 4 casualties after the drill according to named officers."),
         _source("desk-b", "Named officers said Northwind reported 4 casualties after the drill.")],
    )
    assert held["verdicts"][0]["verdict"] == "NEEDS_REVIEW"
    assert held["verdicts"][0]["provisional_verdict"] == "VERIFIED"
    debunked = verify_claims(
        [_claim("Northwind deployed the patrol craft.")],
        [_source("origin", "Northwind deployed the patrol craft."),
         _source("mod", "The ministry said Northwind did not deploy the patrol craft.",
                 category="official_government", primary=True, publisher="Ministry")],
    )
    assert debunked["verdicts"][0]["verdict"] == "CONFLICTING"
    assert debunked["verdicts"][0]["falsity_assessment"] == "DEBUNKED"


def test_media_recycle_mismatch_frames_and_missing_tools(monkeypatch):
    red = _png(8, 8, lambda x, y: (200, 10, 10))
    red_hash = average_hash(red)
    assert red_hash
    near = _png(8, 8, lambda x, y: (210, 12, 12))
    assert hamming(red_hash, average_hash(near)) <= 8
    recycled = verify_media(red, {"event_date": "2024-03-01", "location": "Northwind", "subject": "patrol"},
                            known=[{"asset_id": "old", "sha256": sha256(red), "event_date": "2019-01-01", "average_hash": red_hash}])
    assert recycled["context_status"] == "RECYCLED_OR_OLD"
    assert recycled["authenticity_status"] == "NOT_ASSESSED"
    mismatch = verify_media(
        _png(8, 8, lambda x, y: (10, 10, 200), {"Caption": "Harbour West on 2011-02-02"}),
        {"event_date": "2024-03-01", "location": "Northwind", "subject": "patrol"},
    )
    assert mismatch["context_status"] == "CONTEXT_MISMATCH"
    matched = verify_media(
        _png(8, 8, lambda x, y: (10, 180, 10), {"Caption": "Northwind patrol on 2024-03-01"}),
        {"event_date": "2024-03-01", "location": "Northwind", "subject": "patrol"},
    )
    assert matched["context_status"] == "CONTEXT_MATCHED"
    assert matched["rights"]["status"] == "RIGHTS_REVIEW_REQUIRED"
    assert matched["reverse_search"]["available"] is False
    assert matched["reverse_search"]["matches"] is None
    marker = verify_media(_png(4, 4, lambda x, y: (1, 1, 1), {"DefenceStudioTestSignal": "copied_region"}),
                          {"subject": "marker"})
    assert marker["authenticity_status"] == "MANIPULATION_SUSPECTED"
    frames = verify_media(b"\x00\x00\x00\x18ftypisom", {"subject": "clip"}, frames=[
        {"timestamp_ms": 1500, "bytes": _ppm(4, 4, lambda x, y: (255, 255, 255))},
    ])
    assert frames["frames"][0]["timestamp_ms"] == 1500
    assert frames["frames"][0]["sha256"]
    monkeypatch.setattr("defence_studio.verification.media.shutil.which", lambda name: None)
    bare = verify_media(b"\x00\x00\x00\x18ftypisom", {"subject": "clip", "event_date": "2024-03-01"})
    assert bare["ocr"]["available"] is False
    assert any("ffmpeg" in reason for reason in bare["reasons"])


def test_provider_fallback_does_not_invent_output():
    registry = [
        {"capability": "ocr", "provider": "hash", "available": False, "cost": "none", "limitation": "offline"},
        {"capability": "ocr", "provider": "local-ocr", "available": True, "cost": "local", "limitation": "coarse"},
    ]
    chosen = select_provider("ocr", registry)
    assert chosen["provider"] == "local-ocr"
    assert chosen["fallback_used"] is True
    empty = select_provider("ocr", [dict(registry[0])])
    assert empty["available"] is False
    assert "matches" not in empty or empty.get("matches") is None


def test_budget_blocks_before_a_paid_call():
    with pytest.raises(BudgetExceeded):
        authorize_paid(12.5, 10)
    with pytest.raises(BudgetExceeded):
        authorize_paid(None, 10)
    assert authorize_paid(1.25, 10)["measured"] is False


def _job(tmp_path):
    db = tmp_path / "fact.sqlite3"
    initialize_database(db)
    JobService(db).create(OWNER, "idem-subject", "Northwind drill", "en")
    return db


def test_jobs_are_persistent_idempotent_and_do_not_retry_permanent_failures(tmp_path):
    db = _job(tmp_path)
    subject = JobService(db).list_for_owner(OWNER)[0]["job_id"]
    service = FactcheckService(db, tmp_path / "store")
    first = service.enqueue(OWNER, "run-1", subject, "claims", {
        "claims": [_claim(STATEMENT)],
        "sources": [_source("origin", STATEMENT), _source("desk-a", SUPPORT_A, publisher="Desk A"),
                    _source("desk-b", SUPPORT_B, publisher="Desk B")],
        "collection": {"attempted": True, "errors": []},
    })
    assert service.run_pending() == 1
    done = service.get_job(first["job_id"], OWNER)
    assert done["status"] == "COMPLETED"
    assert done["result"]["verdicts"][0]["verdict"] == "VERIFIED"
    again = FactcheckService(db, tmp_path / "store")
    replay = again.enqueue(OWNER, "run-1", subject, "claims", {
        "claims": [_claim(STATEMENT)],
        "sources": [_source("origin", STATEMENT), _source("desk-a", SUPPORT_A, publisher="Desk A"),
                    _source("desk-b", SUPPORT_B, publisher="Desk B")],
        "collection": {"attempted": True, "errors": []},
    })
    assert replay["job_id"] == first["job_id"]
    packed = service.enqueue(OWNER, "pkg-1", subject, "package", {"topic": "Northwind drill"})
    service.run_pending()
    package = service.latest_package(subject, OWNER)["package"]
    assert package["status"] == "PENDING_REVIEW"
    assert package["machine"]["claims"][0]["verdict"] == "VERIFIED"
    with pytest.raises(PermanentVerificationError):
        service.approve(subject, OWNER, package["package_id"], "  ")
    approved = service.approve(subject, OWNER, package["package_id"], "Reviewed the two independent desks.")
    assert approved["status"] == "APPROVED"
    assert "c1" in approved["approved"]["usable_claim_ids"]
    failed = service.enqueue(OWNER, "bad-package", subject, "package", {"topic": "empty"})
    # Remove claim results so packaging is permanently invalid, then isolate this job's inputs.
    with closing(connect(db)) as connection:
        connection.execute("DELETE FROM verification_runs WHERE kind='claims'")
    service.run_pending()
    failed_view = service.get_job(failed["job_id"], OWNER)
    assert failed_view["status"] == "FAILED"
    assert failed_view["error_class"] == "PERMANENT"
    assert service.run_pending() == 0


def test_package_version_survives_and_blocks_script_until_approval(tmp_path):
    db = _job(tmp_path)
    subject = JobService(db).list_for_owner(OWNER)[0]["job_id"]
    claims = verify_claims(
        [_claim(STATEMENT)],
        [_source("origin", STATEMENT), _source("desk-a", SUPPORT_A), _source("desk-b", SUPPORT_B)],
    )
    first = build_evidence_package(topic="Northwind", subject_job_id=subject, claims=claims, media=[], now="t1")
    second = build_evidence_package(topic="Northwind revised", subject_job_id=subject, claims=claims, media=[], now="t2")
    assert first["content_sha256"] != second["content_sha256"]
    assert usable_claim_ids(first) == ["c1"]
    held = verify_claims([_claim(STATEMENT, category="casualty")], [_source("origin", STATEMENT)])
    assert usable_claim_ids(build_evidence_package(topic="x", subject_job_id=subject, claims=held, media=[], now="t")) == []
    with closing(connect(db)) as connection:
        connection.execute(
            "INSERT INTO verification_packages(package_id, subject_job_id, owner_id, version, content_sha256, "
            "status, machine_json, report_markdown, created_at) VALUES('pkg','subject','owner',1,'abc',"
            "'PENDING_REVIEW','{}','report','t')"
        )
        connection.commit()
        with pytest.raises(Exception, match="explicit human approval"):
            script_gate(connection, "subject", {"eligible_claim_ids": ["c1"], "claims": []})
    preserved = subject
    with closing(connect(db)) as connection:
        connection.execute("DROP TABLE verification_packages")
        connection.execute("DROP TABLE verification_runs")
        connection.execute("DROP TABLE verification_overrides")
        connection.execute("DROP TABLE verification_review_history")
        connection.execute("DROP TABLE verification_script_bindings")
        connection.execute("UPDATE schema_info SET version = 12")
        connection.commit()
    initialize_database(db)
    with closing(connect(db)) as connection:
        assert connection.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION == 13
        assert connection.execute("SELECT topic FROM workflow_jobs WHERE job_id=?", (preserved,)).fetchone()[0] == "Northwind drill"
        assert connection.execute("SELECT COUNT(*) FROM verification_packages").fetchone()[0] == 0


def test_api_requires_auth_and_round_trips_package(tmp_path):
    db = tmp_path / "api.sqlite3"
    store = tmp_path / "artifacts"
    app = create_app(database_file=db, signing_secret=SECRET, storage_root_path=store)
    client = TestClient(app)
    client.__enter__()
    denied = client.get("/api/factcheck/providers")
    assert denied.status_code == 401
    token = access_token(OWNER)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/jobs", headers={**headers, "Idempotency-Key": "subject-1"},
                          json={"topic": "Northwind drill", "language": "en"})
    assert created.status_code == 201
    subject = created.json()["job_id"]
    run = client.post(
        f"/api/jobs/{subject}/factcheck/claims",
        headers={**headers, "Idempotency-Key": "claims-1"},
        json={"claims": [_claim(STATEMENT)], "sources": [
            _source("origin", STATEMENT), _source("desk-a", SUPPORT_A, publisher="Desk A"),
            _source("desk-b", SUPPORT_B, publisher="Desk B"),
        ], "collection": {"attempted": True, "errors": []}},
    )
    assert run.status_code == 202
    assert run.json()["result"]["verdicts"][0]["verdict"] == "VERIFIED"
    image = _png(8, 8, lambda x, y: (10, 180, 10), {"Caption": "Northwind patrol on 2024-03-01"})
    media = client.post(
        f"/api/jobs/{subject}/factcheck/media",
        headers={**headers, "Idempotency-Key": "media-1"},
        files={"file": ("drill.png", image, "image/png")},
        data={"claim_json": json.dumps({"event_date": "2024-03-01", "location": "Northwind", "subject": "patrol"})},
    )
    assert media.status_code == 202
    assert media.json()["result"]["context_status"] == "CONTEXT_MATCHED"
    package = client.post(
        f"/api/jobs/{subject}/factcheck/package",
        headers={**headers, "Idempotency-Key": "pkg-1"},
        json={"topic": "Northwind drill"},
    )
    assert package.status_code == 202
    listed = client.get(f"/api/jobs/{subject}/factcheck/package", headers=headers)
    body = listed.json()["package"]
    assert body["status"] == "PENDING_REVIEW"
    assert "Northwind" in body["report_markdown"]
    blank = client.post(
        f"/api/jobs/{subject}/factcheck/approve",
        headers=headers,
        json={"package_id": body["package_id"], "note": "   "},
    )
    assert blank.status_code == 422
    approved = client.post(
        f"/api/jobs/{subject}/factcheck/approve",
        headers=headers,
        json={"package_id": body["package_id"], "note": "Human reviewer accepted the drill count."},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    history = client.get(f"/api/jobs/{subject}/factcheck/history", headers=headers)
    assert history.status_code == 200
    assert any(row["action"] == "APPROVE" and row["note"] for row in history.json()["history"])
    providers = client.get("/api/factcheck/providers", headers=headers)
    rows = providers.json()["providers"]
    assert any(row["capability"] == "reverse_image_search" and row["available"] is False for row in rows)
    search_rows = [row for row in rows if row["capability"] == "web_search"]
    assert any(row["provider"] == "rss_feed" for row in search_rows)
    hosted = next(row for row in search_rows if row["provider"] == "configured_search_api")
    assert hosted["available"] is False


def test_rights_port_never_clears_without_asset_approval():
    unset = rights_status("asset-1")
    assert unset["status"] == "RIGHTS_REVIEW_REQUIRED"
    assert unset["effective_rights"] is None
    draft = rights_status("asset-1", {"review_status": "DRAFT", "effective_rights": "CLEAR"})
    assert draft["status"] == "RIGHTS_REVIEW_REQUIRED"
    cleared = rights_status("asset-1", {"review_status": "APPROVED", "effective_rights": "CLEAR"})
    assert cleared["status"] == "CLEARED"
    assert cleared["detail"].startswith("AssetService already records")


def test_script_gate_drops_uncleared_claims_and_blocks_empty(tmp_path):
    db = _job(tmp_path)
    subject = JobService(db).list_for_owner(OWNER)[0]["job_id"]
    service = FactcheckService(db, tmp_path / "store")
    service.enqueue(OWNER, "claims-gate", subject, "claims", {
        "claims": [
            _claim(STATEMENT, claim_id="keep"),
            _claim("The municipal library added a reading lamp.", claim_id="drop", entities=["library"]),
        ],
        "sources": [_source("origin", STATEMENT), _source("desk-a", SUPPORT_A, publisher="Desk A"),
                    _source("desk-b", SUPPORT_B, publisher="Desk B")],
        "collection": {"attempted": True, "errors": []},
    })
    service.run_pending()
    service.enqueue(OWNER, "pkg-gate", subject, "package", {"topic": "Northwind drill"})
    service.run_pending()
    package = service.latest_package(subject, OWNER)["package"]
    evidence = {"eligible_claim_ids": ["keep", "drop"], "claims": [{"claim_id": "keep"}, {"claim_id": "drop"}]}
    with closing(connect(db)) as connection:
        with pytest.raises(Exception, match="explicit human approval"):
            script_gate(connection, subject, evidence)
    approved = service.approve(subject, OWNER, package["package_id"], "Accepted the drill count only.")
    usable = approved["approved"]["usable_claim_ids"]
    assert "keep" in usable
    assert "drop" not in usable
    with closing(connect(db)) as connection:
        narrowed, binding = script_gate(connection, subject, evidence)
    assert narrowed["eligible_claim_ids"] == ["keep"]
    assert binding["package_id"] == package["package_id"]
    service.enqueue(OWNER, "pkg-gate-2", subject, "package", {"topic": "Northwind drill revised"})
    service.run_pending()
    latest = service.latest_package(subject, OWNER)
    assert latest["package"]["status"] == "PENDING_REVIEW"
    assert latest["package"]["version"] == 2
    stale = next(row for row in latest["versions"] if row["version"] == 1)
    assert stale["status"] == "STALE"
    with closing(connect(db)) as connection:
        with pytest.raises(Exception, match="explicit human approval"):
            script_gate(connection, subject, evidence)


def test_ffmpeg_frame_pts_when_binary_present(tmp_path):
    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not on PATH")
    clip = tmp_path / "clip.mp4"
    built = subprocess.run(
        [ffmpeg, "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=3", "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, timeout=20, check=False,
    )
    if built.returncode != 0 or not clip.is_file():
        pytest.skip(f"could not build a fixture clip: {(built.stderr or b'')[:200]!r}")
    sampled = sample_video_frames(str(clip))
    assert sampled["available"] is True
    assert sampled["frames"]
    timed = [frame for frame in sampled["frames"] if frame["timestamp_ms"] is not None]
    assert timed, "ffmpeg printed no pts_time; indices must not be treated as timestamps"
    assert timed[0]["timestamp_origin"] == "ffmpeg_showinfo_pts_time"
    assert timed[0]["timestamp_ms"] == 0
    media = verify_media(clip.read_bytes(), {"subject": "fixture"}, frames=sampled["frames"])
    assert media["frames"][0]["timestamp_ms"] == 0
    assert media["rights"]["status"] == "RIGHTS_REVIEW_REQUIRED"


@pytest.mark.skipif(__import__("os").environ.get("DEFENCE_STUDIO_LIVE_FACTCHECK") != "1",
                    reason="opt-in live RSS: set DEFENCE_STUDIO_LIVE_FACTCHECK=1")
def test_live_rss_search_is_real_feed_entries_not_a_web_index():
    import threading
    from defence_studio.acquisition.url_guard import UrlGuard
    from defence_studio.contracts.discovery import DiscoveryFilters, DiscoveryLimits
    from defence_studio.discovery.providers import DiscoveryContext, RssFeedProvider
    provider = RssFeedProvider()
    described = provider.describe()
    assert described["configured"] is True
    outcome, candidates = provider.discover(DiscoveryContext(
        job_id="live-rss", topic="NATO", search_terms=("NATO",), language="en",
        filters=DiscoveryFilters(), limits=DiscoveryLimits(),
        guard=UrlGuard(), cancel=threading.Event(),
    ))
    assert outcome.status.value != "UNAVAILABLE_MISSING_CREDENTIALS"
    if outcome.status.value.startswith("UNAVAILABLE"):
        pytest.fail(f"RSS was expected to be configured: {outcome.message}")
    assert outcome.candidates_returned == len(candidates)
    if candidates:
        assert all(row.requested_url.startswith("http") for row in candidates)
