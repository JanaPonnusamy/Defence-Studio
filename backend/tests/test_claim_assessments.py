"""Phase A: offline claim assessments on disposable databases."""
import sqlite3
from contextlib import closing
from pathlib import Path

from defence_studio.database import SCHEMA_VERSION, connect, initialize_database
from defence_studio.services.assessment import (
    ACTOR,
    SPAN_LIMITATION,
    assess_and_store,
    assess_claim,
)
from defence_studio.services.jobs import JobService

OWNER = "assessment-owner"


def _source(source_id, text, status="NORMALIZED"):
    return {
        "source_id": source_id,
        "status": status,
        "normalized_text": text,
    }


def _claim(claim_id, source_id, text, **extra):
    start = extra.pop("start", 0)
    end = extra.pop("end", len(text))
    body = {
        "claim_id": claim_id,
        "claim_text": text,
        "source_id": source_id,
        "source_published_at": "2024-05-01",
        "qualifiers": [],
        "has_negation": False,
        "entities": {"quantities": [], "designations": []},
        "passage": {"source_id": source_id, "text": text, "start_offset": start, "end_offset": end},
    }
    body.update(extra)
    return body


def _analysis(claims, sources, conflicts=None):
    return {"claims": claims, "normalized_sources": sources, "conflicts": conflicts or []}


def test_span_match_is_not_factual_support():
    text = "The squadron trained together."
    claim = _claim("c1", "s1", text)
    other = _source("s2", "A second independent page.")
    row = assess_claim(claim, _analysis([claim], [_source("s1", text), other]))
    assert row["outcome"] == "PARTIALLY_SUPPORTED"
    assert row["outcome"] != "SUPPORTED"
    assert text in row["explanation"]
    assert "0-31" in row["explanation"] or f"0-{len(text)}" in row["explanation"]
    assert SPAN_LIMITATION in row["explanation"]
    assert SPAN_LIMITATION in row["limitations"]
    assert row["actor"] == ACTOR


def test_quantity_hedge_and_missing_date_need_review():
    text = "The unit has 12 aircraft."
    quantity = _claim("q", "s1", text, entities={"quantities": ["12 aircraft"], "designations": []})
    hedge = _claim("h", "s1", "Officials reportedly confirmed the drill.", qualifiers=["reportedly"])
    undated = _claim("u", "s1", "The squadron trained together.", source_published_at=None)
    sources = [_source("s1", text + " Officials reportedly confirmed the drill. The squadron trained together."),
               _source("s2", "Second source.")]
    # Each claim's passage must match its own source text. Use one source per case.
    for claim, source_text, reason in (
        (quantity, text, "quantity"),
        (hedge, "Officials reportedly confirmed the drill.", "hedge"),
        (undated, "The squadron trained together.", "publication date"),
    ):
        row = assess_claim(claim, _analysis([claim], [_source("s1", source_text), _source("s2", "Second source.")]))
        assert row["outcome"] == "NEEDS_REVIEW", reason
        assert SPAN_LIMITATION in row["explanation"]
        assert "SUPPORTED" != row["outcome"]


def test_single_source_needs_review_even_when_the_span_matches():
    text = "The squadron trained together."
    claim = _claim("c1", "s1", text)
    row = assess_claim(claim, _analysis([claim], [_source("s1", text)]))
    assert row["outcome"] == "NEEDS_REVIEW"
    assert "fewer than two normalized sources" in row["explanation"]


def test_stored_conflict_marks_both_claims_contradicted():
    left = "The battery is operational."
    right = "The battery is not operational."
    a = _claim("a", "s1", left)
    b = _claim("b", "s2", right)
    conflicts = [{"group_id": "g1", "claim_id_a": "a", "claim_id_b": "b"}]
    analysis = _analysis([a, b], [_source("s1", left), _source("s2", right)], conflicts)
    first, second = assess_claim(a, analysis), assess_claim(b, analysis)
    assert first["outcome"] == second["outcome"] == "CONTRADICTED"
    assert "a" in second["explanation"] and "b" in first["explanation"]
    assert left in first["explanation"] and right in first["explanation"]
    assert "Neither side is verified." in first["explanation"]


def test_bad_offset_is_unsupported_and_does_not_quote_outside_text():
    claim = _claim("c1", "s1", "Alpha", start=0, end=99)
    claim["passage"]["text"] = "Alpha"
    row = assess_claim(claim, _analysis([claim], [_source("s1", "Alpha bravo outside.")]))
    assert row["outcome"] == "UNSUPPORTED"
    assert "Alpha" not in row["explanation"]
    assert "bravo" not in row["explanation"]
    assert row["passage_sha256"] == ""


def test_migration_preserves_human_reviews_and_adds_empty_assessments(tmp_path):
    db = tmp_path / "v10.sqlite3"
    initialize_database(db)
    JobService(db).create(OWNER, "legacy", "Rafale", "en")
    with closing(connect(db)) as con:
        job_id = con.execute("SELECT job_id FROM workflow_jobs").fetchone()[0]
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO claim_reviews VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, "claim-1", "APPROVED_FOR_USE", "analysis_result", "a" * 64,
             "human-reviewer", "inspected the passage", 2, "t0", "t1"),
        )
        con.execute(
            "INSERT INTO claim_review_history VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hist-1", job_id, "claim-1", "APPROVED_FOR_USE", "analysis_result", "a" * 64,
             "human-reviewer", "inspected the passage", 2, None, "t1", "idem-1"),
        )
        con.execute("DROP TABLE claim_assessments")
        con.execute("UPDATE schema_info SET version = 10")
        con.commit()
    initialize_database(db)
    with closing(connect(db)) as con:
        assert con.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION == 13
        review = con.execute("SELECT decision, note, reviewer_id, revision FROM claim_reviews").fetchone()
        assert tuple(review) == ("APPROVED_FOR_USE", "inspected the passage", "human-reviewer", 2)
        history = con.execute(
            "SELECT decision, note, reviewer_id, revision, idempotency_key FROM claim_review_history"
        ).fetchone()
        assert tuple(history) == ("APPROVED_FOR_USE", "inspected the passage", "human-reviewer", 2, "idem-1")
        assert con.execute("SELECT COUNT(*) FROM claim_assessments").fetchone()[0] == 0


def test_persistence_is_idempotent_and_versions_by_analysis_hash(tmp_path):
    db = tmp_path / "assess.sqlite3"
    initialize_database(db)
    job_id = JobService(db).create(OWNER, "job", "Rafale", "en")["job_id"]
    text = "The squadron trained together."
    claim = _claim("c1", "s1", text)
    analysis = _analysis([claim], [_source("s1", text), _source("s2", "Another page.")])
    first_hash = "a" * 64
    before_reviews = _counts(db)
    first = assess_and_store(db, job_id, analysis, first_hash)
    again = assess_and_store(db, job_id, analysis, first_hash)
    assert len(first) == len(again) == 1
    assert first[0]["assessment_id"] == again[0]["assessment_id"]
    assert first[0]["outcome"] == "PARTIALLY_SUPPORTED"
    second = assess_and_store(db, job_id, analysis, "b" * 64)
    assert second[0]["assessment_id"] != first[0]["assessment_id"]
    with closing(connect(db)) as con:
        assert con.execute("SELECT COUNT(*) FROM claim_assessments").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM claim_assessments WHERE outcome='SUPPORTED'").fetchone()[0] == 0
        assert _counts(db) == before_reviews
    module = Path(__file__).resolve().parents[1] / "defence_studio" / "services" / "assessment.py"
    source = module.read_text(encoding="utf-8")
    assert "openai" not in source.lower()
    assert "httpx" not in source
    assert "decide_claim" not in source
    assert "claim_reviews" not in source
    assert "claim_review_history" not in source


def _counts(db):
    with closing(sqlite3.connect(db)) as con:
        reviews = con.execute("SELECT COUNT(*) FROM claim_reviews").fetchone()[0]
        history = con.execute("SELECT COUNT(*) FROM claim_review_history").fetchone()[0]
    return reviews, history
