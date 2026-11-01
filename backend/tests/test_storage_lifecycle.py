"""Storage lifecycle: registry, retention, dry-run, confirmed cleanup (schema v15).

Every test uses a temporary database and storage tree. Nothing touches data/.
"""
import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, access_token
from defence_studio.api.app import create_app
from defence_studio.database import SCHEMA_VERSION, connect, initialize_database
from defence_studio.storage.lifecycle import StorageError, StorageLifecycleService

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, value=T0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **kw):
        self.value += timedelta(**kw)


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "db.sqlite3"
    root = tmp_path / "storage"
    root.mkdir()
    initialize_database(db)
    clock = Clock()
    svc = StorageLifecycleService(db, root, now=clock, min_free_bytes=0)
    return db, root, clock, svc


def add_job(db, job_id, status="COMPLETED", owner="owner-1"):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO workflow_jobs(job_id,owner_id,idempotency_key,request_schema,request_hash,topic,language,"
                  "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (job_id, owner, "k-" + job_id, "job.v1", "h", "t", "en", status, T0.isoformat(), T0.isoformat()))


def add_integration(db, job_id, kind, status, uri=None, sha=None, version=1):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO integration_artifacts(job_id,language,kind,version,status,uri,sha256,input_binding,"
                  "created_by,created_at,updated_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (job_id, "en", kind, version, status, uri, sha, "{}", "test", T0.isoformat(), T0.isoformat(),
                   f"{kind}-{version}"))


def write(root, rel, data=b"x" * 100, age_days=0):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    ts = (T0 - timedelta(days=age_days)).timestamp()
    os.utime(path, (ts, ts))
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidates(svc, owner="owner-1"):
    run = svc.dry_run(owner)
    return run, {c["path_relative"] for c in run["candidates"]}


# 0. migration ----------------------------------------------------------------

def test_fresh_and_upgraded_databases_reach_v15_with_storage_tables(tmp_path):
    assert SCHEMA_VERSION == 15
    db = tmp_path / "fresh.sqlite3"
    initialize_database(db)
    initialize_database(db)  # idempotent
    with connect(db) as c:
        assert c.execute("SELECT version FROM schema_info").fetchone()[0] == 15
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"storage_artifacts", "storage_retention_policy", "storage_cleanup_runs",
            "storage_cleanup_items", "speech_reference_sources"} <= tables


def test_v14_database_upgrades_without_touching_existing_rows(tmp_path):
    db = tmp_path / "v14.sqlite3"
    initialize_database(db)
    add_job(db, "j-keep")
    with sqlite3.connect(db) as c:
        for t in ("storage_cleanup_items", "storage_cleanup_runs", "storage_retention_policy", "storage_artifacts"):
            c.execute(f"DROP TABLE {t}")
        c.execute("UPDATE schema_info SET version = 14")
    initialize_database(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT version FROM schema_info").fetchone()[0] == 15
        assert c.execute("SELECT count(*) FROM workflow_jobs WHERE job_id='j-keep'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM storage_artifacts").fetchone()[0] == 0


# 1-2. registry and permanent protection ---------------------------------------

def test_registry_records_every_file_with_class_hash_and_state(env):
    db, root, clock, svc = env
    add_job(db, "j1")
    html = write(root, "jobs/j1/research/raw/page.html", b"<html>a</html>", age_days=1)
    write(root, "jobs/j1/evidence/package.json", b"{}", age_days=400)
    summary = svc.summary()
    assert summary["file_count"] == 2 and summary["total_bytes"] == html.stat().st_size + 2
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        rows = {r["rel_path"]: dict(r) for r in c.execute("SELECT * FROM storage_artifacts")}
    raw = rows["jobs/j1/research/raw/page.html"]
    assert raw["retention_class"] == "RAW_ARTICLE_HTML" and raw["job_id"] == "j1"
    assert raw["sha256"] == sha(html) and raw["lifecycle_state"] == "TEMPORARY" and raw["preserved"] == 0
    assert raw["expires_at"] == (T0 + timedelta(days=7)).isoformat()  # clock starts at first sighting
    ev = rows["jobs/j1/evidence/package.json"]
    assert ev["retention_class"] == "RESEARCH_EVIDENCE" and ev["preserved"] == 1 and ev["lifecycle_state"] == "PRESERVED"


def test_permanent_classes_are_never_candidates_and_cannot_be_unlocked(env):
    db, root, clock, svc = env
    add_job(db, "j1")
    for rel in ("jobs/j1/evidence/e.json", "jobs/j1/package/final.mp4", "jobs/j1/scripts/s.json",
                "jobs/j1/render/en/out.mp4", "jobs/j1/unknown/u.bin"):
        write(root, rel, age_days=5000)
    clock.advance(days=5000)
    run, paths = candidates(svc)
    assert paths == set() and run["confirm_phrase"] == "DELETE 0 FILES"
    for name in ("FINAL_VIDEO", "RESEARCH_EVIDENCE", "SCRIPT", "ACCEPTED_IMAGE", "UNCLASSIFIED"):
        with pytest.raises(StorageError) as exc:
            svc.update_retention("owner-1", {name: 1})
        assert exc.value.status_code == 422


# 3-4. active job and running render -------------------------------------------

@pytest.mark.parametrize("status", ["RUNNING", "AWAITING_REVIEW", "PAUSED", "QUEUED"])
def test_files_of_a_non_terminal_job_are_protected(env, status):
    db, root, clock, svc = env
    add_job(db, "j1", status=status)
    write(root, "jobs/j1/research/raw/page.html")
    clock.advance(days=30)
    run, paths = candidates(svc)
    assert paths == set()
    assert run["skipped"][0]["reason"] == f"JOB_NOT_TERMINAL:{status}"


@pytest.mark.parametrize("status", ["RUNNING", "QUEUED", "INTERRUPTED"])
def test_job_with_a_running_render_is_protected_even_when_completed(env, status):
    db, root, clock, svc = env
    add_job(db, "j1")
    add_integration(db, "j1", "render", status)
    write(root, "jobs/j1/render/frames/f0001.png")
    write(root, "jobs/j1/research/raw/page.html")
    clock.advance(days=30)
    run, paths = candidates(svc)
    assert paths == set()
    assert {s["reason"] for s in run["skipped"]} == {"JOB_HAS_ACTIVE_WORK"}


# 5. expired temporary cleanup, referenced evidence ------------------------------

def test_expired_unreferenced_raw_html_is_deleted_and_referenced_html_is_kept(env):
    db, root, clock, svc = env
    add_job(db, "j1")
    loose = write(root, "jobs/j1/research/raw/loose.html")
    cited = write(root, "jobs/j1/research/raw/cited.html")
    write(root, "jobs/j1/research/acquisition_result.json",
          b'{"artifacts": [{"uri": "jobs/j1/research/raw/cited.html"}]}')
    svc.summary()
    clock.advance(days=6)
    assert candidates(svc)[1] == set()  # 7-day class not yet elapsed
    clock.advance(days=2)
    run, paths = candidates(svc)
    assert paths == {"jobs/j1/research/raw/loose.html"}
    assert {s["path_relative"]: s["reason"] for s in run["skipped"]}["jobs/j1/research/raw/cited.html"] == "REFERENCED"
    result = svc.execute("owner-1", run["run_id"], "DELETE 1 FILES")
    assert [d["path_relative"] for d in result["deleted"]] == ["jobs/j1/research/raw/loose.html"]
    assert result["reclaimed_bytes"] == 100 and not loose.exists() and cited.exists()


def test_reference_by_basename_in_a_database_row_protects_the_file(env):
    db, root, clock, svc = env
    add_job(db, "j1")
    write(root, "jobs/j1/research/raw/abc123.html")
    add_integration(db, "j1", "qc_report", "SUCCEEDED", uri="elsewhere/abc123.html", sha="0" * 64)
    clock.advance(days=30)
    run, paths = candidates(svc)
    assert paths == set() and run["skipped"][0]["reason"] == "REFERENCED"


# 6. orphans -------------------------------------------------------------------

def test_orphans_are_reported_and_never_deleted(env):
    db, root, clock, svc = env
    orphan = write(root, "jobs/no-such-job/research/raw/page.html", age_days=900)
    write(root, "image-research/no-job/img.jpg", age_days=900)
    clock.advance(days=900)
    summary = svc.summary()
    assert summary["orphan_bytes"] == 200
    run, paths = candidates(svc)
    assert paths == set()
    assert {s["reason"] for s in run["skipped"]} == {"ORPHAN_REPORT_ONLY"}
    assert orphan.exists()


# 7. path safety -----------------------------------------------------------------

@pytest.mark.parametrize("rel", ["../outside.txt", "jobs/../../outside.txt", "/etc/passwd", "C:/Windows/x",
                                 "jobs//x", "jobs\\..\\..\\x"])
def test_unsafe_paths_are_rejected(env, rel):
    _, _, _, svc = env
    with pytest.raises(StorageError):
        svc._safe_path(rel)


def test_a_tampered_dry_run_item_outside_the_root_is_blocked(env, tmp_path):
    db, root, clock, svc = env
    add_job(db, "j1")
    write(root, "jobs/j1/research/raw/page.html")
    outside = tmp_path / "outside.html"
    outside.write_bytes(b"x" * 100)
    svc.summary()  # first registry sighting starts the retention clock
    clock.advance(days=8)
    run, _ = candidates(svc)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE storage_cleanup_items SET rel_path='../outside.html' WHERE run_id=?", (run["run_id"],))
    result = svc.execute("owner-1", run["run_id"], "DELETE 1 FILES")
    assert result["deleted"] == [] and len(result["blocked"]) == 1
    assert outside.exists()


def test_symlinked_directories_are_not_followed(env, tmp_path):
    db, root, clock, svc = env
    add_job(db, "j1")
    target = tmp_path / "victim"
    write(target, "page.html")
    link = root / "jobs" / "j1" / "research"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this account")
    clock.advance(days=30)
    assert svc.summary()["file_count"] == 0
    assert (target / "page.html").exists()


# 8-9. dry-run accuracy, confirmation, idempotency -----------------------------

def test_dry_run_touches_nothing_and_execute_requires_the_exact_phrase(env):
    db, root, clock, svc = env
    add_job(db, "j1")
    files = [write(root, f"jobs/j1/research/raw/p{i}.html") for i in range(3)]
    svc.summary()  # first registry sighting starts the retention clock
    clock.advance(days=8)
    run, paths = candidates(svc)
    assert len(paths) == 3 and run["would_reclaim_bytes"] == 300 and all(f.exists() for f in files)
    for bad in ("DELETE 2 FILES", "delete 3 files", "DELETE 3 FILES "):
        with pytest.raises(StorageError) as exc:
            svc.execute("owner-1", run["run_id"], bad)
        assert exc.value.status_code == 422
    with pytest.raises(StorageError) as exc:
        svc.execute("owner-2", run["run_id"], "DELETE 3 FILES")
    assert exc.value.status_code == 404
    assert all(f.exists() for f in files)
    result = svc.execute("owner-1", run["run_id"], "DELETE 3 FILES")
    assert {d["path_relative"] for d in result["deleted"]} == paths and not any(f.exists() for f in files)


def test_execute_is_idempotent_and_reports_externally_removed_files(env):
    db, root, clock, svc = env
    add_job(db, "j1")
    a = write(root, "jobs/j1/research/raw/a.html")
    b = write(root, "jobs/j1/research/raw/b.html")
    svc.summary()  # first registry sighting starts the retention clock
    clock.advance(days=8)
    run, _ = candidates(svc)
    b.unlink()
    first = svc.execute("owner-1", run["run_id"], "DELETE 2 FILES")
    assert [d["path_relative"] for d in first["deleted"]] == ["jobs/j1/research/raw/a.html"]
    assert [d["path_relative"] for d in first["already_gone"]] == ["jobs/j1/research/raw/b.html"]
    second = svc.execute("owner-1", run["run_id"], "anything")
    assert second["replayed"] is True and second["run_id"] == first["run_id"]
    assert not a.exists()
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM storage_cleanup_runs WHERE kind='EXECUTE'").fetchone()[0] == 1


def test_changed_content_job_state_or_stale_dry_run_blocks_deletion(env):
    db, root, clock, svc = env
    add_job(db, "j1")
    changed = write(root, "jobs/j1/research/raw/changed.html")
    svc.summary()  # first registry sighting starts the retention clock
    clock.advance(days=8)
    run, _ = candidates(svc)
    changed.write_bytes(b"y" * 100)
    os.utime(changed, ((T0 - timedelta(days=30)).timestamp(),) * 2)
    result = svc.execute("owner-1", run["run_id"], "DELETE 1 FILES")
    assert result["deleted"] == [] and changed.exists()

    write(root, "jobs/j1/research/raw/reopened.html")
    svc.summary()
    clock.advance(days=8)
    run2, paths = candidates(svc)
    assert "jobs/j1/research/raw/reopened.html" in paths
    with sqlite3.connect(db) as c:
        c.execute("UPDATE workflow_jobs SET status='RUNNING' WHERE job_id='j1'")
    result = svc.execute("owner-1", run2["run_id"], f"DELETE {len(paths)} FILES")
    assert result["deleted"] == [] and "JOB_NOT_TERMINAL" in result["blocked"][0]["reason"]

    with sqlite3.connect(db) as c:
        c.execute("UPDATE workflow_jobs SET status='COMPLETED' WHERE job_id='j1'")
    run3, paths3 = candidates(svc)
    clock.advance(minutes=31)
    with pytest.raises(StorageError) as exc:
        svc.execute("owner-1", run3["run_id"], f"DELETE {len(paths3)} FILES")
    assert exc.value.status_code == 409


def test_locked_file_failure_is_recorded_and_retried_later(env, monkeypatch):
    db, root, clock, svc = env
    add_job(db, "j1")
    f = write(root, "jobs/j1/research/raw/locked.html")
    svc.summary()  # first registry sighting starts the retention clock
    clock.advance(days=8)
    run, _ = candidates(svc)
    real_unlink = Path.unlink

    def deny(self, *a, **k):
        raise PermissionError("in use")
    monkeypatch.setattr(Path, "unlink", deny)
    result = svc.execute("owner-1", run["run_id"], "DELETE 1 FILES")
    assert result["status"] == "COMPLETED_WITH_ERRORS" and result["errors"] and f.exists()
    monkeypatch.setattr(Path, "unlink", real_unlink)
    retry, paths = candidates(svc)
    assert paths == {"jobs/j1/research/raw/locked.html"}
    assert svc.execute("owner-1", retry["run_id"], "DELETE 1 FILES")["deleted"]
    assert svc.summary()["cleanup_errors"]  # the failed run stays in history


# 12. disk capacity --------------------------------------------------------------

def test_insufficient_disk_space_is_reported(tmp_path):
    db = tmp_path / "db.sqlite3"
    initialize_database(db)
    svc = StorageLifecycleService(db, tmp_path, min_free_bytes=10 ** 18)
    assert svc.capacity()["sufficient"] is False
    assert svc.summary()["roots"][0]["below_floor"] is True
    assert StorageLifecycleService(db, tmp_path, min_free_bytes=0).capacity(1)["sufficient"] is True


# 13-15. render success / failure, integrity -------------------------------------

def test_intermediates_go_only_after_a_verified_render_and_failed_ones_wait_three_days(env):
    db, root, clock, svc = env
    add_job(db, "ok")
    out = write(root, "jobs/ok/render/en/out.mp4", b"video")
    frame = write(root, "jobs/ok/render/frames/f1.png")
    add_integration(db, "ok", "render", "SUCCEEDED", uri="jobs/ok/render/en/out.mp4", sha="0" * 64)
    add_integration(db, "ok", "qc_report", "SUCCEEDED", version=1)
    assert "jobs/ok/render/frames/f1.png" not in candidates(svc)[1]  # checksum mismatch: not verified
    with sqlite3.connect(db) as c:
        c.execute("UPDATE integration_artifacts SET sha256=? WHERE kind='render'", (sha(out),))
    assert "jobs/ok/render/frames/f1.png" in candidates(svc)[1]

    add_job(db, "bad", status="FAILED")
    add_integration(db, "bad", "render", "FAILED")
    write(root, "jobs/bad/render/tmp/seg.mp4")
    assert "jobs/bad/render/tmp/seg.mp4" not in candidates(svc)[1]
    clock.advance(days=3, seconds=1)
    run, paths = candidates(svc)
    assert {"jobs/bad/render/tmp/seg.mp4", "jobs/ok/render/frames/f1.png"} <= paths
    before = sha(out)
    svc.execute("owner-1", run["run_id"], f"DELETE {len(paths)} FILES")
    assert not frame.exists() and sha(out) == before  # final output untouched


def test_rejected_image_files_expire_and_accepted_images_never_do(env):
    db, root, clock, svc = env
    with sqlite3.connect(db) as c:
        c.executescript("""
            CREATE TABLE image_research_jobs (job_id TEXT PRIMARY KEY, owner_id TEXT, status TEXT);
            CREATE TABLE image_research_candidates (image_id TEXT, job_id TEXT, review_status TEXT,
                                                    stored_relative_path TEXT);
            CREATE TABLE image_research_reviews (review_id TEXT, image_id TEXT, created_at TEXT);
        """)
        c.execute("INSERT INTO image_research_jobs VALUES('ij','owner-1','COMPLETED')")
        c.execute("INSERT INTO image_research_candidates VALUES('acc','ij','ACCEPTED','image-research/ij/acc.jpg')")
        c.execute("INSERT INTO image_research_candidates VALUES('rej','ij','REJECTED','image-research/ij/rej.jpg')")
        c.execute("INSERT INTO image_research_candidates VALUES('pen','ij','PENDING_REVIEW','image-research/ij/pen.jpg')")
        c.execute("INSERT INTO image_research_reviews VALUES('r1','rej',?)", ((T0 + timedelta(days=10)).isoformat(),))
    for name in ("acc", "rej", "pen"):
        write(root, f"image-research/ij/{name}.jpg", age_days=100)
    svc.summary()  # first registry sighting starts the retention clock
    clock.advance(days=16)
    assert candidates(svc)[1] == set()  # rejected 6 days ago
    clock.advance(days=2)
    run, paths = candidates(svc)
    assert paths == {"image-research/ij/rej.jpg"}
    svc.execute("owner-1", run["run_id"], "DELETE 1 FILES")
    assert (root / "image-research/ij/acc.jpg").exists() and (root / "image-research/ij/pen.jpg").exists()


def test_cleanup_never_crosses_owners(env):
    db, root, clock, svc = env
    add_job(db, "mine")
    add_job(db, "theirs", owner="owner-2")
    write(root, "jobs/mine/research/raw/a.html")
    write(root, "jobs/theirs/research/raw/b.html")
    svc.summary()  # first registry sighting starts the retention clock
    clock.advance(days=8)
    run, paths = candidates(svc, "owner-1")
    assert paths == {"jobs/mine/research/raw/a.html"}
    assert {s["reason"] for s in run["skipped"]} == {"NOT_OWNER"}


# retention configuration ---------------------------------------------------------

def test_retention_is_configurable_validated_and_persistent(env):
    db, root, clock, svc = env
    out = svc.update_retention("owner-1", {"RAW_ARTICLE_HTML": 2, "TEMP_LOG": None})
    by_name = {c["name"]: c for c in out["categories"]}
    assert by_name["RAW_ARTICLE_HTML"]["days"] == 2 and by_name["TEMP_LOG"]["preserve"] is True
    assert by_name["RENDER_INTERMEDIATE"]["mode"] == "AFTER_VERIFIED_RENDER"
    for bad in ({"RAW_ARTICLE_HTML": 0}, {"RAW_ARTICLE_HTML": -1}, {"RAW_ARTICLE_HTML": "soon"},
                {"NOPE": 3}, {"RAW_ARTICLE_HTML": "after_verified_render"}):
        with pytest.raises(StorageError):
            svc.update_retention("owner-1", bad)
    fresh = StorageLifecycleService(db, root, now=clock)
    assert {c["name"]: c["days"] for c in fresh.retention()["categories"]}["RAW_ARTICLE_HTML"] == 2
    add_job(db, "j1")
    write(root, "jobs/j1/research/raw/p.html")
    svc.summary()
    clock.advance(days=2, seconds=1)
    assert candidates(svc)[1] == {"jobs/j1/research/raw/p.html"}


# 10. API + restart persistence ---------------------------------------------------

def test_api_flow_and_history_survive_an_app_restart(tmp_path):
    db = tmp_path / "db.sqlite3"
    storage = tmp_path / "storage"
    headers = {"Authorization": f"Bearer {access_token('owner-1')}"}
    app = create_app(db, SECRET, storage_root_path=storage)
    with TestClient(app) as client:
        assert client.get("/api/storage/summary").status_code in (401, 403)
        summary = client.get("/api/storage/summary", headers=headers)
        assert summary.status_code == 200 and summary.json()["file_count"] == 0
        assert client.get("/api/storage/render-controls", headers=headers).json()["max_simultaneous_renders"] is None
        assert client.put("/api/storage/retention", headers=headers,
                          json={"categories": {"FINAL_VIDEO": 3}}).status_code == 422
        dry = client.post("/api/storage/cleanup/dry-run", headers=headers).json()
        assert dry["dry_run"] is True and dry["candidates"] == []
        refused = client.post("/api/storage/cleanup", headers=headers,
                              json={"dry_run_id": dry["run_id"], "confirm": "DELETE 0 FILES"})
        assert refused.status_code == 409
        assert client.get("/api/storage/jobs/unknown", headers=headers).status_code == 404
    restarted = create_app(db, SECRET, storage_root_path=storage)
    with TestClient(restarted) as client:
        history = client.get("/api/storage/cleanup/history", headers=headers).json()["cleanups"]
        assert [h["run_id"] for h in history] == [dry["run_id"]] and history[0]["dry_run"] is True
