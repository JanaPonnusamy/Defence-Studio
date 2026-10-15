"""Voice Analyzer/Generation integration: schema v12, queue reuse, API, recovery, cancel, ownership."""
import io
import shutil
import sqlite3
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, access_token
from defence_studio.api.app import create_app
from defence_studio.database import SCHEMA_VERSION, connect, initialize_database
from defence_studio.services import voice_engine as ve
from defence_studio.services.voice import VoiceService
from defence_studio.services.worker import ClaimLost, Worker, WorkerService

SR = 16000
REPO = Path(__file__).resolve().parents[2]


def speech_wav() -> bytes:
    def phrase(n, f0, syl=0.18):
        out = []
        for _ in range(n):
            t = np.arange(int(syl * SR)) / SR
            s = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3)) * np.sin(np.pi * t / syl) ** 2
            out += [0.15 * s, np.zeros(int(0.04 * SR))]
        return np.concatenate(out)
    gap = lambda ms: np.zeros(int(ms / 1000 * SR))
    x = np.concatenate([gap(300), phrase(8, 190), gap(200), phrase(8, 230), gap(700), phrase(10, 200, 0.12), gap(300)])
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return b.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_VOICE_TEST_TONE", "1")
    ve._catalog_cache.update(at=0.0, voices=None, models={})
    db = tmp_path / "ds.sqlite3"
    app = create_app(db, SECRET, storage_root_path=tmp_path / "storage")
    with TestClient(app) as client:
        voice = VoiceService(db, tmp_path / "storage")
        worker = Worker(db, "test-voice-worker", voice.handlers(), retry_safe_stages=set(voice.handlers()))
        yield {"c": client, "db": db, "voice": voice, "worker": worker, "root": tmp_path / "storage"}


def H(owner="user-1", key="k1"):
    return {"Authorization": f"Bearer {access_token(owner)}", "Idempotency-Key": key}


def upload(env, key="a1", owner="user-1", name="ref.wav", data=None, **form):
    f = {"profile_name": "ref style", "language": "ta-IN", "authorization_ref": "LOCAL-TEST-AUTH"}
    f.update(form)
    return env["c"].post("/api/voice/analysis", headers=H(owner, key), data=f,
                         files={"file": (name, data if data is not None else speech_wav(), "audio/wav")})


def direction(**kw):
    d = {"voice_id": ve.TEST_TONE_ID, "language": "ta-IN", "tempo": 1.0, "pitch_variation": 0.5, "energy": 0.7,
         "emotion": {"label": "SERIOUS", "intensity": 0.7}, "emphasis": [], "pauses": []}
    d.update(kw)
    return d


# ------------------------------------------------------------------ migration
def test_schema_is_12_and_tables_exist(env):
    with sqlite3.connect(env["db"]) as c:
        assert c.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION >= 12
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"voice_job_requests", "voice_sources", "voice_profiles", "voice_profile_versions",
            "voice_style_segments", "voice_generations", "voice_alignments", "voice_licenses"} <= names


def _downgrade_to_11(path):
    c = sqlite3.connect(path)
    for t in ("voice_job_requests", "voice_generation_segments", "voice_alignments", "voice_generations",
              "voice_model_versions", "voice_models", "voice_licenses", "voice_style_segments",
              "voice_profile_versions", "voice_profiles", "voice_sources"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.execute("UPDATE schema_info SET version=11")
    c.commit(); c.close()


def test_migration_v11_to_v12_preserves_rows(tmp_path):
    db = tmp_path / "m.sqlite3"
    initialize_database(db)
    c = connect(db)
    c.execute("INSERT INTO workflow_jobs(job_id,owner_id,idempotency_key,request_schema,request_hash,topic,language,"
              "status,created_at,updated_at) VALUES('j1','o','k','defence-studio.job-create.v1','h','topic','ta','DRAFT','t','t')")
    c.close()
    _downgrade_to_11(db)
    initialize_database(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION
        assert c.execute("SELECT job_id, topic FROM workflow_jobs").fetchall() == [("j1", "topic")]
        assert c.execute("SELECT count(*) FROM voice_job_requests").fetchone()[0] == 0
    initialize_database(db)   # idempotent re-run


def test_migration_of_copy_of_real_database(tmp_path):
    src = REPO / "data" / "defence_studio.sqlite3"
    if not src.exists():
        pytest.skip("no local database to copy")
    dst = tmp_path / "copy.sqlite3"
    shutil.copy(src, dst)
    with sqlite3.connect(dst) as c:
        before = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("workflow_jobs", "workflow_stages")}
    initialize_database(dst)
    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION
        after = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert before == after


def test_voice_jobs_do_not_appear_in_research_job_list(env):
    r = upload(env)
    assert r.status_code == 202
    jobs = env["c"].get("/api/jobs", headers=H()).json()
    assert all("voice" not in j.get("topic", "").lower() for j in (jobs if isinstance(jobs, list) else jobs.get("jobs", [])))


# ------------------------------------------------------------------- analysis
def test_analysis_job_runs_on_existing_queue_and_persists(env):
    r = upload(env)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "QUEUED" and job["kind"] == "ANALYSIS" and job["stage"]["key"] == "voice_analysis"
    with sqlite3.connect(env["db"]) as c:   # same queue table
        assert c.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (job["job_id"],)).fetchone()[0] == "QUEUED"
    assert env["worker"].run_once() is True
    done = env["c"].get(f"/api/voice/jobs/{job['job_id']}", headers=H()).json()
    assert done["status"] == "COMPLETED" and done["result"]["segments"] == 3
    vid = done["result_ref"]
    p = env["c"].get(f"/api/voice/profiles/{vid}", headers=H()).json()
    assert p["read_only"] and p["version"] == 1 and p["provenance"]["state"] == "AUTHORIZED"
    assert p["provenance"]["raw_audio_retained"] is False and p["limitations"]
    segs = env["c"].get(f"/api/voice/profiles/{vid}/segments", headers=H()).json()["segments"]
    assert len(segs) == 3
    assert not list(env["voice"].upload_dir.glob("*"))      # temporary reference removed after success
    assert done["reference_temp_retained"] is False


def test_duplicate_submission_is_idempotent(env):
    a = upload(env, key="same").json()
    b = upload(env, key="same").json()
    assert a["job_id"] == b["job_id"]
    assert len(list(env["voice"].upload_dir.glob("*"))) == 1   # redundant upload copy dropped
    r = upload(env, key="same", profile_name="different")
    assert r.status_code == 409


def test_upload_validation(env):
    assert upload(env, name="x.txt").status_code == 422
    assert upload(env, key="e", data=b"").status_code == 422
    r = env["c"].post("/api/voice/analysis", headers=H(key="z"),
                      data={"profile_name": "p", "language": "ta-IN", "authorization_ref": " "},
                      files={"file": ("a.wav", speech_wav(), "audio/wav")})
    assert r.status_code in (422,)
    assert not list(env["voice"].upload_dir.glob("*"))


def test_invalid_audio_fails_job_with_error(env):
    j = upload(env, key="bad", data=b"not audio at all").json()
    for _ in range(5):
        env["worker"].run_once()
    got = env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()
    assert got["status"] in ("FAILED", "QUEUED")
    assert got["error"] and "INVALID_AUDIO" in got["error"] or "decode" in (got["error"] or "").lower()
    assert got["result_ref"] is None


def test_owner_isolation(env):
    j = upload(env).json()
    env["worker"].run_once()
    vid = env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()["result_ref"]
    other = H("user-2", "x")
    assert env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=other).status_code == 404
    assert env["c"].get(f"/api/voice/profiles/{vid}", headers=other).status_code == 404
    assert env["c"].get("/api/voice/profiles", headers=other).json()["profiles"] == []
    assert env["c"].get("/api/voice/jobs", headers=other).json()["jobs"] == []
    assert env["c"].get("/api/voice/jobs").status_code in (401, 403)


def test_profile_versions_compare_and_immutability(env):
    j1 = upload(env, key="v1").json()
    env["worker"].run_once()
    vid = env["c"].get(f"/api/voice/jobs/{j1['job_id']}", headers=H()).json()["result_ref"]
    j2 = upload(env, key="v2", base_voice_style_id=vid, transcript="one two three four five").json()
    env["worker"].run_once()
    p = env["c"].get(f"/api/voice/profiles/{vid}", headers=H()).json()
    assert p["version"] == 2 and [v["version"] for v in p["versions"]] == [1, 2]
    v1 = env["c"].get(f"/api/voice/profiles/{vid}?version=1", headers=H()).json()
    assert v1["speaking_rate"]["words_per_minute"] is None and p["speaking_rate"]["words_per_minute"] > 0
    cmp = env["c"].get(f"/api/voice/profiles/{vid}/compare?a=1&b=2", headers=H()).json()
    assert cmp["identical"] is False and any(m["metric"] == "speaking_rate.words_per_minute" for m in cmp["metrics"])
    assert env["c"].get(f"/api/voice/profiles/{vid}/compare?a=1&b=1", headers=H()).status_code == 422
    with sqlite3.connect(env["db"]) as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE voice_profile_versions SET profile_json='{}'")


# ------------------------------------------------------------------ generation
def test_generation_job_audio_alignment_and_history(env):
    r = env["c"].post("/api/voice/generations", headers=H(key="g1"),
                      json={"script": "One two three. Four five six.", "direction": direction()})
    assert r.status_code == 202, r.text
    job = r.json()
    assert env["worker"].run_once() is True
    done = env["c"].get(f"/api/voice/jobs/{job['job_id']}", headers=H()).json()
    assert done["status"] == "COMPLETED"
    gid = done["result_ref"]
    g = env["c"].get(f"/api/voice/generations/{gid}", headers=H()).json()
    assert g["status"] == "COMPLETED" and g["sample_rate"] == 48000 and g["duration_ms"] > 500
    assert g["effective_direction"]["tempo"] == 1.0 and g["voice"]["natural_speech"] is False
    audio = env["c"].get(g["audio_url"], headers=H())
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert env["c"].get(g["audio_url"], headers=H("user-2", "q")).status_code == 404
    al = env["c"].get(f"/api/voice/generations/{gid}/alignment", headers=H()).json()
    assert al["segments"][0]["accuracy"] == "MEASURED_RENDER_BOUNDARY"
    assert all(w["accuracy"] == "ESTIMATED_PROPORTIONAL" for w in al["words"])
    assert al["forced_alignment"]["available"] is False and al["aligner"]["forced_alignment_run"] is False
    hist = env["c"].get("/api/voice/jobs?kind=GENERATION", headers=H()).json()["jobs"]
    assert [j["job_id"] for j in hist] == [job["job_id"]]


def test_generation_rejects_unsupported_and_invalid(env):
    c = env["c"]
    body = lambda d: {"script": "Hello there.", "direction": d}
    assert c.post("/api/voice/generations", headers=H(key="a"), json=body(direction(voice_id="NOPE"))).status_code == 422
    assert c.post("/api/voice/generations", headers=H(key="b"), json=body(direction(tempo=9))).status_code == 422
    assert c.post("/api/voice/generations", headers=H(key="c"), json=body(direction(language="xx-XX"))).status_code == 422
    r = c.post("/api/voice/generations", headers=H(key="d"), json=body(direction(voice_id=ve.NARRATOR_ID)))
    assert r.status_code == 422 and "not available" in r.json()["detail"]
    assert c.post("/api/voice/generations", headers=H(key="e"), json={"script": "  ", "direction": direction()}).status_code in (422,)
    assert c.post("/api/voice/generations", headers=H(key="f"),
                  json=body(direction(style_profile_id="VSP-doesnotexist"))).status_code == 404


def test_tamil_not_claimed_for_os_voice(env):
    prov = env["c"].get("/api/voice/providers", headers=H()).json()
    for v in prov["voices"]:
        assert v["tamil_verified"] is False
        if v["kind"] == "OS_VOICE":
            assert "ta-IN" not in v["languages"]
    assert next(v for v in prov["voices"] if v["voice_id"] == ve.NARRATOR_ID)["kind"] == "NOT_PROVISIONED"
    assert prov["aligner"]["available"] is False and prov["limitations"]


def test_test_tone_hidden_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFENCE_STUDIO_VOICE_TEST_TONE", raising=False)
    ve._catalog_cache.update(at=0.0, voices=None, models={})
    assert all(v["voice_id"] != ve.TEST_TONE_ID for v in ve.catalog(refresh=True))


# -------------------------------------------- cancel / retry / recovery / failure
def test_cancel_queued_job_never_runs(env):
    j = env["c"].post("/api/voice/generations", headers=H(key="c1"),
                      json={"script": "Cancel me.", "direction": direction()}).json()
    c = env["c"].post(f"/api/voice/jobs/{j['job_id']}/cancel", headers=H()).json()
    assert c["status"] == "CANCELLED" and c["can_retry"]
    assert env["worker"].run_once() is False
    assert env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()["result_ref"] is None
    with sqlite3.connect(env["db"]) as con:
        assert con.execute("SELECT count(*) FROM voice_generations").fetchone()[0] == 0


def test_cancel_running_job_cannot_commit(env):
    j = env["c"].post("/api/voice/generations", headers=H(key="c2"),
                      json={"script": "Running job.", "direction": direction()}).json()
    ws = WorkerService(env["db"])
    ctx = ws.claim_next("w", {"voice_generation"})
    assert ctx and ctx.job_id == j["job_id"]
    env["c"].post(f"/api/voice/jobs/{j['job_id']}/cancel", headers=H())
    with pytest.raises(ClaimLost):
        ws.complete(ctx, [])
    assert env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()["status"] == "CANCELLED"
    # handler honours cancellation even before the commit point
    import threading
    with pytest.raises(Exception):
        env["voice"].generation_handler(ctx, threading.Event())
    with sqlite3.connect(env["db"]) as con:
        assert con.execute("SELECT count(*) FROM voice_generations WHERE status='COMPLETED'").fetchone()[0] == 0


def test_generic_stage_transition_cannot_revive_cancelled_voice_job(env):
    job = env["c"].post("/api/voice/generations", headers=H(key="cancelled-stage"),
                        json={"script": "Already cancelled.", "direction": direction()}).json()
    cancelled = env["c"].post(f"/api/voice/jobs/{job['job_id']}/cancel", headers=H())
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"

    revived = env["c"].post(
        f"/api/jobs/{job['job_id']}/stages/voice_generation/transitions",
        headers=H(), json={"status": "RUNNING"},
    )

    assert revived.status_code == 409
    assert env["c"].get(f"/api/voice/jobs/{job['job_id']}", headers=H()).json()["status"] == "CANCELLED"


def test_restart_recovery_requeues_expired_claim(env):
    j = env["c"].post("/api/voice/generations", headers=H(key="r1"),
                      json={"script": "Recover me.", "direction": direction()}).json()
    ws = WorkerService(env["db"])
    ctx = ws.claim_next("dead-worker", {"voice_generation"}, lease_seconds=1)
    assert ctx
    assert env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()["status"] == "RUNNING"
    ws.recover_expired(datetime.now(timezone.utc) + timedelta(seconds=30))     # "restart"
    with sqlite3.connect(env["db"]) as con:      # skip the worker's retry back-off for the test
        con.execute("UPDATE workflow_stages SET retry_after=NULL WHERE job_id=?", (j["job_id"],))
    assert env["worker"].run_once() is True
    got = env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()
    assert got["status"] == "COMPLETED" and got["result_ref"]


def test_failure_is_reported_and_retry_creates_new_job(env):
    ve.catalog()
    m = ve._catalog_cache["models"][ve.TEST_TONE_ID][0]
    m.fail_on = "boom"
    j = env["c"].post("/api/voice/generations", headers=H(key="f1"),
                      json={"script": "this goes boom.", "direction": direction()}).json()
    env["worker"].run_once()
    got = env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()
    assert got["error"] and "synthetic failure" in got["error"] and got["result_ref"] is None
    env["c"].post(f"/api/voice/jobs/{j['job_id']}/cancel", headers=H())       # stop automatic retries
    assert env["c"].post(f"/api/voice/jobs/{j['job_id']}/retry", headers=H(key="f2")).status_code == 202
    m.fail_on = None
    assert env["worker"].run_once() is True
    jobs = env["c"].get("/api/voice/jobs?kind=GENERATION", headers=H()).json()["jobs"]
    new = next(x for x in jobs if x["retry_of"] == j["job_id"])
    assert new["status"] == "COMPLETED"
    assert env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()["status"] == "CANCELLED"   # history intact
    assert env["c"].post(f"/api/voice/jobs/{new['job_id']}/retry", headers=H(key="f3")).status_code == 409


def test_retry_analysis_uses_retained_reference_then_cleans_up(env):
    j = upload(env, key="ra", data=b"garbage bytes").json()
    env["worker"].run_once()
    env["c"].post(f"/api/voice/jobs/{j['job_id']}/cancel", headers=H())
    got = env["c"].get(f"/api/voice/jobs/{j['job_id']}", headers=H()).json()
    assert got["reference_temp_retained"] is True           # retained only while a retry is possible
    d = env["c"].delete(f"/api/voice/jobs/{j['job_id']}/reference", headers=H()).json()
    assert d["reference_temp_retained"] is False and not list(env["voice"].upload_dir.glob("*"))
    assert env["c"].post(f"/api/voice/jobs/{j['job_id']}/retry", headers=H(key="rb")).status_code == 409


def test_purge_stale_uploads(env):
    orphan = env["voice"].upload_dir / "orphan.wav"
    orphan.write_bytes(b"x")
    import os, time
    os.utime(orphan, (time.time() - 3600, time.time() - 3600))
    assert env["voice"].purge_stale_uploads() == 1 and not orphan.exists()
