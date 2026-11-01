"""Voice Lab backend: schema, identities, datasets, training gate, prosody, style transfer, generation,
Script Studio contract, mixer projects (edit/undo/persist/export), storage and recovery.

TEST-ONLY FIXTURES: `speechlike()` is a harmonic signal with a controlled pitch contour, syllable-rate
amplitude modulation and pauses. It is NOT speech; it lets measurements be checked against known truth.
`FakeTTS` stands in for the model runtime so the pipeline can be tested without weights. Real-model
inference is verified separately by backend/scripts/voice_lab_benchmark.py (VL-1).
"""
import io
import os
import wave
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, access_token
from defence_studio.api.app import create_app
from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab import hardware, prosody as P, style as S
from defence_studio.voice_lab.generation import chunk_text

class _FreshAuth(Mapping):
    """Auth headers that mint a new token on every use. conftest tokens expire after 600 s, and a full-suite
    run can reach this module long after import, so a token built once at import time would be stale."""

    def __init__(self, subject):
        self.subject = subject

    def __getitem__(self, key):
        if key != "Authorization":
            raise KeyError(key)
        return f"Bearer {access_token(self.subject)}"

    def __iter__(self):
        return iter(("Authorization",))

    def __len__(self):
        return 1


H = _FreshAuth("owner-1")
H2 = _FreshAuth("owner-2")


def speechlike(phrases, base_hz, sr=24000, lead_ms=300, seed=0):
    """phrases: [(dur_ms, rel_semitones, pause_after_ms)]. Returns float32 mono."""
    rng = np.random.default_rng(seed)
    out = [rng.normal(0, 3e-4, int(sr * lead_ms / 1000))]
    for dur, st, pause in phrases:
        n = int(sr * dur / 1000)
        t = np.arange(n) / sr
        f0 = base_hz * 2 ** (st / 12) * (1 + 0.03 * np.sin(2 * np.pi * 1.5 * t))
        ph = 2 * np.pi * np.cumsum(f0) / sr
        x = sum(np.sin(k * ph) / k for k in range(1, 9))
        env = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t) ** 2
        edge = np.minimum(1, np.minimum(t, t[::-1]) / 0.03)
        out.append((0.25 * x * env * edge + rng.normal(0, 3e-4, n)).astype(np.float32))
        out.append(rng.normal(0, 3e-4, int(sr * pause / 1000)))
    return np.concatenate(out).astype(np.float32)


def wav(x, sr=24000):
    return A.wav_bytes(x, sr)


class FakeTTS:
    """Test-only runtime: one speech-like phrase per sentence at a fixed 'female' pitch."""
    runtime = "parler"
    skip_install_check = True

    def __init__(self, base_hz=220.0):
        self.base_hz, self.calls = base_hz, 0

    def synthesize(self, model, cfg, text, out_wav, seed):
        self.calls += 1
        n = max(1, len([s for s in text.replace("?", ".").split(".") if s.strip()]))
        x = speechlike([(1000 + 200 * i, 0.0, 150) for i in range(n)], self.base_hz, lead_ms=100, seed=seed)
        Path(out_wav).write_bytes(wav(x))
        return {"ok": True, "gen_s": 0.01, "audio_s": len(x) / 24000, "rtf": 0.01, "sample_rate": 24000}


class FakeTrainer:
    """Test-only trainer: writes checkpoints, honours cancel. Verifies orchestration, not training quality."""
    runtime = "parler"
    supports_pause = True

    def describe(self, model):
        return {"adapter": "test-fake"}

    def run(self, job, manifest_path, workdir, resume_from, log, cancel, on_checkpoint, on_metrics):
        for step in (1, 2):
            if cancel.is_set():
                raise RuntimeError("cancelled")
            ck = Path(workdir) / f"checkpoint-{step}"
            ck.mkdir(exist_ok=True)
            (ck / "weights.bin").write_bytes(f"step{step}".encode() * 10)
            on_checkpoint(ck)
            on_metrics({"step": step, "loss": 1.0 / step})
        return {"final_checkpoint": str(ck), "metrics": {"final_loss": 0.5}}


CAPABLE_HW = {"cpu": {"name": "test"}, "ram_bytes": 64 * 1024 ** 3, "cuda_usable": True, "rocm_usable": False,
              "gpus": [{"name": "Test GPU", "vendor": "NVIDIA", "vram_bytes": 48 * 1024 ** 3}],
              "disks": [{"path": "/", "free_bytes": 500 * 1024 ** 3}], "torch": {"cuda_available": True},
              "cloud_training_enabled": False, "measured_at": "test"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_VOICE_LAB_WORKER", "0")
    monkeypatch.setenv("DEFENCE_STUDIO_VOICE_LAB_RESERVE_BYTES", "0")
    monkeypatch.setenv("DEFENCE_STUDIO_VOICE_LAB_MIN_DATASET_MINUTES", "0.05")
    db, store = tmp_path / "vl.sqlite3", tmp_path / "store"

    def make():
        app = create_app(db, SECRET, store)
        return app

    app = make()
    with TestClient(app) as c:
        svc = app.state.voice_lab
        svc.backends["parler"] = FakeTTS()
        yield {"c": c, "svc": svc, "make": make, "db": db, "store": store, "tmp": tmp_path}


def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json()


def narrator(c, name, gender, speaker, headers=H):
    con = ok(c.post("/api/voice-lab/consents", headers=headers, json={
        "subject_name": name, "subject_role": "NARRATOR", "scope": ["TRAINING", "GENERATION", "COMMERCIAL"],
        "document_ref": f"consent/{name}.pdf"}), 201)
    n = ok(c.post("/api/voice-lab/narrators", headers=headers, json={
        "display_name": name, "gender_label": gender, "languages": ["ta", "en"], "consent_id": con["consent_id"]}), 201)
    v = ok(c.post(f"/api/voice-lab/narrators/{n['narrator_id']}/versions", headers=headers, json={
        "model_id": "indic-parler-tts", "speaker": speaker, "description": f"{speaker} speaks clearly"}), 201)
    return n, con, v


# --------------------------------------------------------------------------- schema
def test_schema_is_additive_and_versioned(env):
    with closing(env["svc"].ctx.conn()) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"vl_voice_profiles", "vl_model_versions", "vl_audio_projects", "vl_model_licences"} <= tables
        before = c.execute("SELECT sql FROM sqlite_master WHERE name='voice_profiles'").fetchone()
        assert c.execute("SELECT value FROM vl_schema_meta WHERE key='version'").fetchone()[0] == "1"
    from defence_studio.voice_lab.schema import ensure_schema
    with closing(env["svc"].ctx.conn()) as c:
        ensure_schema(c)  # idempotent
        assert c.execute("SELECT sql FROM sqlite_master WHERE name='voice_profiles'").fetchone() == before
        lic = {r[0]: r[1] for r in c.execute("SELECT model_id, commercial_status FROM vl_model_licences")}
    assert lic["f5-tts-base"] == "NOT_ELIGIBLE" and lic["mms-tts-tam"] == "NOT_ELIGIBLE"
    assert lic["indic-parler-tts"] == "PROVISIONAL"


def test_models_endpoint_keeps_licences_separate(env):
    models = {m["model_id"]: m for m in ok(env["c"].get("/api/voice-lab/models", headers=H))["models"]}
    ip = models["indic-parler-tts"]
    assert ip["code_licence"] == "Apache-2.0" and ip["weight_licence"] == "Apache-2.0" and ip["data_licence"] == "UNVERIFIED"
    assert ip["gated"] is True and ip["production_allowed"] is False
    assert models["indicf5"]["code_licence"] == "NONE_DETECTED" and models["indicf5"]["commercial"] == "EVALUATION_ONLY"


# ------------------------------------------------------------------------ identities
def test_multiple_narrators_identity_never_shared(env):
    c = env["c"]
    m, _, mv = narrator(c, "Male Narrator", "male", "Prakash")
    f, _, fv = narrator(c, "Female Narrator", "female", "Jaya")
    third, _, _ = narrator(c, "Third Narrator", None, "Kavitha")  # no hard-coded limit of two
    assert len(ok(c.get("/api/voice-lab/narrators", headers=H))["narrators"]) == 3
    r = c.post(f"/api/voice-lab/narrators/{f['narrator_id']}/versions", headers=H,
               json={"model_id": "indic-parler-tts", "speaker": "prakash", "description": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IDENTITY_IN_USE"
    # a version of another narrator can never be activated or used
    r = c.post(f"/api/voice-lab/narrators/{f['narrator_id']}/versions/{mv['model_version_id']}/activate", headers=H)
    assert r.status_code == 409
    r = c.post("/api/voice-lab/generations", headers=H, json={"narrator_id": f["narrator_id"], "text": "வணக்கம்.",
                                                              "model_version_id": mv["model_version_id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IDENTITY_MISMATCH"
    # rollback: second base version, then re-activate the first
    v2 = ok(c.post(f"/api/voice-lab/narrators/{f['narrator_id']}/versions", headers=H,
                   json={"model_id": "indic-parler-tts", "speaker": "Jaya-2", "description": "softer"}), 201)
    assert v2["active"] and v2["version"] == 2
    n = ok(c.post(f"/api/voice-lab/narrators/{f['narrator_id']}/versions/{fv['model_version_id']}/activate", headers=H))
    assert n["active_model_version_id"] == fv["model_version_id"]
    # other owners cannot see it
    assert c.get(f"/api/voice-lab/narrators/{f['narrator_id']}", headers=H2).status_code == 404
    # archive / restore
    assert ok(c.post(f"/api/voice-lab/narrators/{third['narrator_id']}/archive", headers=H))["status"] == "ARCHIVED"
    assert len(ok(c.get("/api/voice-lab/narrators", headers=H))["narrators"]) == 2


# --------------------------------------------------------------------------- datasets
def _dataset(c, svc, n, con, name="main"):
    return ok(c.post("/api/voice-lab/datasets", headers=H, json={"narrator_id": n["narrator_id"], "name": name,
                                                                  "language": "ta", "consent_id": con["consent_id"]}), 201)


def _upload(c, did, data, name="take1.wav", note="studio session 1, consent on file"):
    return c.post(f"/api/voice-lab/datasets/{did}/files", headers=H, files={"file": (name, data, "audio/wav")},
                  data={"source_note": note})


def test_dataset_ingest_segment_qc_duplicates_versions(env):
    c, svc = env["c"], env["svc"]
    n, con, _ = narrator(c, "Female Narrator", "female", "Jaya")
    ds = _dataset(c, svc, n, con)
    x = speechlike([(2500, 0, 1000), (3000, 2, 1100), (2200, -1, 1000)], 220)
    up = ok(_upload(c, ds["dataset_id"], wav(x)), 202)
    assert up["status"] == "QUEUED" and up["qc"]["clipping_ratio"] == 0
    assert svc.run_pending() == 1
    segs = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/segments", headers=H))
    assert segs["total"] == 3 and all(s["alignment_status"] == "NOT_ALIGNED" for s in segs["items"])
    assert all(s["qc_status"] in ("PASS", "WARN") for s in segs["items"])
    # exact duplicate file rejected; louder copy of the same take -> segment duplicates flagged
    assert _upload(c, ds["dataset_id"], wav(x)).status_code == 409
    ok(_upload(c, ds["dataset_id"], wav(x * 0.8), name="take1-copy.wav"), 202)
    svc.run_pending()
    segs = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/segments", headers=H))["items"]
    assert sum(s["duplicate_of"] is not None for s in segs) == 3
    # clipped audio fails QC
    clipped = np.clip(speechlike([(2500, 0, 500)], 220, seed=3) * 8, -1, 1)
    ok(_upload(c, ds["dataset_id"], wav(clipped), name="clipped.wav"), 202)
    svc.run_pending()
    segs = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/segments", headers=H))["items"]
    assert any(s["qc_status"] == "FAIL" and "CLIPPING" in s["qc"]["issues"] for s in segs)
    rd = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/readiness", headers=H))
    assert rd["status"] == "NOT_READY" and any(m["code"] == "TRANSCRIPTS_INCOMPLETE" for m in rd["missing"])
    assert c.post(f"/api/voice-lab/datasets/{ds['dataset_id']}/versions", headers=H).status_code == 409
    # transcripts: non-Tamil text warns; reviewed transcripts make segments eligible
    usable = [s for s in segs if s["qc_status"] != "FAIL" and s["duplicate_of"] is None]
    w = ok(c.patch(f"/api/voice-lab/segments/{usable[0]['segment_id']}", headers=H, json={"transcript": "hello"}))
    assert "TRANSCRIPT_NOT_MOSTLY_TAMIL_SCRIPT" in w["warnings"]
    assert c.patch(f"/api/voice-lab/segments/{segs[0]['segment_id']}", headers=H,
                   json={"transcript": "", "transcript_status": "REVIEWED"}).status_code == 422
    for s in segs:
        if s["qc_status"] == "FAIL" or s["duplicate_of"]:
            ok(c.patch(f"/api/voice-lab/segments/{s['segment_id']}", headers=H, json={"excluded": True}))
        else:
            ok(c.patch(f"/api/voice-lab/segments/{s['segment_id']}", headers=H,
                       json={"transcript": "இது ஒரு சோதனை வாக்கியம்.", "transcript_status": "REVIEWED"}))
    rd = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/readiness", headers=H))
    assert rd["status"] == "READY", rd
    v = ok(c.post(f"/api/voice-lab/datasets/{ds['dataset_id']}/versions", headers=H), 201)
    assert v["stats"]["segments"] == 3 and sum(v["stats"]["splits"].values()) == 3
    assert v["status"] == "PENDING_APPROVAL"
    v = ok(c.post(f"/api/voice-lab/dataset-versions/{v['dataset_version_id']}/approve", headers=H))
    assert v["status"] == "APPROVED"
    # tampering with the manifest is detected
    (env["store"] / v["manifest_rel_path"]).write_text("{}")
    v2 = ok(c.post(f"/api/voice-lab/datasets/{ds['dataset_id']}/versions", headers=H), 201)
    (env["store"] / v2["manifest_rel_path"]).write_text("tampered")
    r = c.post(f"/api/voice-lab/dataset-versions/{v2['dataset_version_id']}/approve", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MANIFEST_INTEGRITY"


def test_clearing_a_transcript_cannot_mark_it_reviewed(env):
    """T0-A regression: an empty new transcript must not borrow the stored one to pass the REVIEWED check."""
    c, svc = env["c"], env["svc"]
    n, con, _ = narrator(c, "Female Narrator", "female", "Jaya")
    ds = _dataset(c, svc, n, con)
    ok(_upload(c, ds["dataset_id"], wav(speechlike([(2500, 0, 1000)], 220))), 202)
    svc.run_pending()
    sid = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/segments", headers=H))["items"][0]["segment_id"]
    ok(c.patch(f"/api/voice-lab/segments/{sid}", headers=H, json={"transcript": "இது ஒரு சோதனை வாக்கியம்."}))
    for cleared in ("", "   "):
        r = c.patch(f"/api/voice-lab/segments/{sid}", headers=H, json={"transcript": cleared, "transcript_status": "REVIEWED"})
        assert r.status_code == 422, r.text
    seg = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/segments", headers=H))["items"][0]
    assert seg["transcript"] == "இது ஒரு சோதனை வாக்கியம்." and seg["transcript_status"] == "DRAFT"  # rejected call changed nothing
    # status-only REVIEWED still uses the stored transcript; clearing without REVIEWED still works
    assert ok(c.patch(f"/api/voice-lab/segments/{sid}", headers=H, json={"transcript_status": "REVIEWED"}))["updated"] == ["transcript_status"]
    ok(c.patch(f"/api/voice-lab/segments/{sid}", headers=H, json={"transcript": ""}))
    seg = ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/segments", headers=H))["items"][0]
    assert seg["transcript"] is None and seg["transcript_status"] == "NONE"
    assert c.patch(f"/api/voice-lab/segments/{sid}", headers=H, json={"transcript_status": "REVIEWED"}).status_code == 422


def test_datasets_never_mix_identities_and_need_narrator_consent(env):
    c, svc = env["c"], env["svc"]
    m, mcon, _ = narrator(c, "Male Narrator", "male", "Prakash")
    f, fcon, _ = narrator(c, "Female Narrator", "female", "Jaya")
    mds, fds = _dataset(c, svc, m, mcon), _dataset(c, svc, f, fcon)
    take = wav(speechlike([(2500, 0, 500)], 120))
    ok(_upload(c, mds["dataset_id"], take), 202)
    r = _upload(c, fds["dataset_id"], take)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IDENTITY_MIXING"
    ref_con = ok(c.post("/api/voice-lab/consents", headers=H, json={
        "subject_name": "Ref", "subject_role": "REFERENCE_SPEAKER", "scope": ["ANALYSIS", "TRAINING"],
        "document_ref": "ref.pdf"}), 201)
    r = c.post("/api/voice-lab/datasets", headers=H, json={"narrator_id": f["narrator_id"], "name": "ref",
                                                          "language": "ta", "consent_id": ref_con["consent_id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CONSENT_ROLE"
    ok(c.post(f"/api/voice-lab/consents/{fcon['consent_id']}/revoke", headers=H))
    assert ok(c.get(f"/api/voice-lab/datasets/{fds['dataset_id']}/readiness", headers=H))["status"] == "BLOCKED"


# --------------------------------------------------------------------------- training
def _approved_version(c, svc, n, con, name="main"):
    ds = _dataset(c, svc, n, con, name)
    ok(_upload(c, ds["dataset_id"], wav(speechlike([(2500, 0, 600), (2600, 1, 600)], 220, seed=hash(name) % 99))), 202)
    svc.run_pending()
    for s in ok(c.get(f"/api/voice-lab/datasets/{ds['dataset_id']}/segments", headers=H))["items"]:
        ok(c.patch(f"/api/voice-lab/segments/{s['segment_id']}", headers=H,
                   json={"transcript": "தமிழ் வாக்கியம்.", "transcript_status": "REVIEWED"}))
    v = ok(c.post(f"/api/voice-lab/datasets/{ds['dataset_id']}/versions", headers=H), 201)
    return ok(c.post(f"/api/voice-lab/dataset-versions/{v['dataset_version_id']}/approve", headers=H))


def test_training_blocked_on_this_machine_without_creating_a_job(env):
    hw = hardware.detect(env["store"])
    if hw["cuda_usable"]:
        pytest.skip("this machine has CUDA; the hardware-blocked path is covered by injected hardware below")
    c, svc = env["c"], env["svc"]
    f, fcon, _ = narrator(c, "Female Narrator", "female", "Jaya")
    v = _approved_version(c, svc, f, fcon)
    body = {"narrator_id": f["narrator_id"], "dataset_version_id": v["dataset_version_id"]}
    pf = ok(c.post("/api/voice-lab/training-jobs/preflight", headers=H, json=body))
    assert not pf["ready"] and "NO_CUDA_GPU" in {b["code"] for b in pf["blockers"]}
    r = c.post("/api/voice-lab/training-jobs", headers={**H, "Idempotency-Key": "t1"}, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TRAINING_BLOCKED"
    assert ok(c.get("/api/voice-lab/training-jobs", headers=H))["training_jobs"] == []
    sysinfo = ok(c.get("/api/voice-lab/system", headers=H))
    assert sysinfo["training_supported"] is False and "NO_CUDA_GPU" in sysinfo["training_blockers"]
    cloud = ok(c.post("/api/voice-lab/training-jobs/preflight", headers=H, json={**body, "target": "cloud"}))
    assert {b["code"] for b in cloud["blockers"]} >= {"CLOUD_DISABLED"}
    r = c.post("/api/voice-lab/training", headers=H, json={"identity": "Female Narrator", "consentRef": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TRAINING_BLOCKED"


def test_training_orchestration_on_capable_hardware_is_per_narrator(env):
    c, svc = env["c"], env["svc"]
    svc.training.hardware_probe = lambda: CAPABLE_HW
    svc.training.adapters = {"parler": FakeTrainer()}
    m, mcon, _ = narrator(c, "Male Narrator", "male", "Prakash")
    f, fcon, fv = narrator(c, "Female Narrator", "female", "Jaya")
    mver, fver = _approved_version(c, svc, m, mcon, "m"), _approved_version(c, svc, f, fcon, "f")
    r = c.post("/api/voice-lab/training-jobs", headers={**H, "Idempotency-Key": "x"},
               json={"narrator_id": f["narrator_id"], "dataset_version_id": mver["dataset_version_id"]})
    assert r.status_code == 409 and "IDENTITY_MISMATCH" in {b["code"] for b in r.json()["detail"]["blockers"]}
    job = ok(c.post("/api/voice-lab/training-jobs", headers={**H, "Idempotency-Key": "f1"},
                    json={"narrator_id": f["narrator_id"], "dataset_version_id": fver["dataset_version_id"]}), 202)
    assert job["status"] == "QUEUED"
    r = c.post("/api/voice-lab/training-jobs", headers={**H, "Idempotency-Key": "m1"},
               json={"narrator_id": m["narrator_id"], "dataset_version_id": mver["dataset_version_id"]})
    assert r.status_code == 409 and "CONCURRENCY_LIMIT" in {b["code"] for b in r.json()["detail"]["blockers"]}
    svc.run_pending()
    job = ok(c.get(f"/api/voice-lab/training-jobs/{job['training_job_id']}", headers=H))
    assert job["status"] == "COMPLETED" and len(job["metrics"]["checkpoints"]) == 2
    assert "start training" in ok(c.get(f"/api/voice-lab/training-jobs/{job['training_job_id']}/log", headers=H))["log"]
    vers = ok(c.get(f"/api/voice-lab/narrators/{f['narrator_id']}/versions", headers=H))["versions"]
    tuned = [v for v in vers if v["kind"] == "FINE_TUNED"]
    assert len(tuned) == 1 and tuned[0]["training_job_id"] == job["training_job_id"]
    assert tuned[0]["parent_version_id"] == fv["model_version_id"] and not tuned[0]["active"]
    assert ok(c.get(f"/api/voice-lab/narrators/{m['narrator_id']}/versions", headers=H))["versions"][0]["kind"] == "BASE_SPEAKER"
    # the male narrator now trains independently
    mj = ok(c.post("/api/voice-lab/training-jobs", headers={**H, "Idempotency-Key": "m2"},
                   json={"narrator_id": m["narrator_id"], "dataset_version_id": mver["dataset_version_id"]}), 202)
    ok(c.post(f"/api/voice-lab/training-jobs/{mj['training_job_id']}/cancel", headers=H))
    assert ok(c.get(f"/api/voice-lab/training-jobs/{mj['training_job_id']}", headers=H))["status"] == "CANCELLED"


# ---------------------------------------------------------------- prosody / style
REF_PHRASES = [(1100, 3.0, 500), (1000, -2.0, 700), (1500, 1.0, 400), (1500, -3.0, 600)]


def test_reference_analysis_measures_known_delivery(env):
    c, svc = env["c"], env["svc"]
    x = speechlike(REF_PHRASES, 110, lead_ms=400)
    r = c.post("/api/voice-lab/references", headers=H, files={"file": ("ref.wav", wav(x), "audio/wav")},
               data={"name": "Male reference", "authorization_ref": "owner-recorded 2026-10-30"})
    ref = ok(r, 202)
    assert c.post("/api/voice-lab/references", headers=H, files={"file": ("r.wav", wav(x), "audio/wav")},
                  data={"name": "x", "authorization_ref": ""}).status_code == 422
    svc.run_pending()
    an = ok(c.get(f"/api/voice-lab/references/{ref['reference_id']}/prosody?frames=true", headers=H))
    s = an["summary"]
    assert s["phrase_count"] == 4
    assert 100 < s["f0_median_hz"] < 125
    durs = [p["duration_ms"] for p in an["phrases"]]
    for got, (want, _, _) in zip(durs, REF_PHRASES):
        assert abs(got - want) < 80, durs
    pauses = [p["pause_after_ms"] for p in an["phrases"][:-1]]
    for got, (_, _, want) in zip(pauses, REF_PHRASES[:-1]):
        assert abs(got - want) < 80, pauses
    rel = [p["rel_median_st"] for p in an["phrases"]]
    assert np.corrcoef(rel, [p[1] for p in REF_PHRASES])[0, 1] > 0.98
    assert s["emotion"]["kind"] == "INFERENCE" and an["separation"]["status"] == "NOT_AVAILABLE"
    assert len(an["frames"]["f0_hz"]) > 400
    assert any(g["classification"] == "SPEECH" for g in an["regions"])


def _style(c, svc):
    x = speechlike(REF_PHRASES, 110, lead_ms=400)
    ref = ok(c.post("/api/voice-lab/references", headers=H, files={"file": ("ref.wav", wav(x), "audio/wav")},
                    data={"name": "Male reference", "authorization_ref": "owner"}), 202)
    svc.run_pending()
    st = ok(c.post("/api/voice-lab/style-profiles", headers=H,
                   json={"reference_id": ref["reference_id"], "name": "Briefing delivery"}), 201)
    return ref, st


def test_style_profile_human_review_and_immutability(env):
    c, svc = env["c"], env["svc"]
    _, st = _style(c, svc)
    assert st["features"]["emotion"]["label_kind"] == "INFERENCE"
    assert "provenance_only" in st["features"] and "speaker" not in str(st["features"]).lower().replace("speaker identity", "")
    assert c.patch(f"/api/voice-lab/style-profiles/{st['style_profile_id']}/labels", headers=H,
                   json={"feature_key": "timing", "value": {}}).status_code == 422
    st2 = ok(c.patch(f"/api/voice-lab/style-profiles/{st['style_profile_id']}/labels", headers=H,
                     json={"feature_key": "emotion", "value": {"label": "AUTHORITATIVE", "intensity": 0.6}}))
    assert st2["features"]["emotion"]["label_kind"] == "HUMAN" and st2["features_sha256"] != st["features_sha256"]
    ok(c.post(f"/api/voice-lab/style-profiles/{st['style_profile_id']}/approve", headers=H))
    assert c.patch(f"/api/voice-lab/style-profiles/{st['style_profile_id']}/labels", headers=H,
                   json={"feature_key": "emotion", "value": {}}).status_code == 409


def test_female_narrator_takes_male_delivery_but_keeps_her_pitch(env):
    c, svc = env["c"], env["svc"]
    f, _, _ = narrator(c, "Female Narrator", "female", "Jaya")
    _, st = _style(c, svc)
    text = "முதல் வரி. இரண்டாம் வரி. மூன்றாம் வரி. நான்காம் வரி."
    body = {"narrator_id": f["narrator_id"], "text": text, "style_profile_id": st["style_profile_id"]}
    r = c.post("/api/voice-lab/generations", headers=H, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "STYLE_NOT_APPROVED"
    ok(c.post(f"/api/voice-lab/style-profiles/{st['style_profile_id']}/approve", headers=H))
    plain = ok(c.post("/api/voice-lab/generations", headers=H, json={**body, "style_profile_id": None}), 202)
    g = ok(c.post("/api/voice-lab/generations", headers=H, json=body), 202)
    svc.run_pending()
    g = ok(c.get(f"/api/voice-lab/generations/{g['generation_id']}", headers=H))
    assert g["status"] == "COMPLETED", g
    rep = g["timing"]["style_report"]
    assert rep["mode"] == "PHRASE_MAPPED" and len(rep["phrases"]) == 4
    assert rep["stretch_capped_phrases"] >= 1  # 1000/1200 needs 0.83 < 0.85 cap
    for p in rep["phrases"]:
        assert 0.85 - 1e-9 <= p["stretch_applied"] <= 1.15 + 1e-9
    cmp_ = rep["comparison"]
    assert cmp_["relative_pitch_correlation"] > 0.9, cmp_
    assert cmp_["output_f0_median_hz"] > 170, "female output must not be pulled to the male absolute pitch"
    assert cmp_["phrase_start_error_ms"]["max"] < 250
    assert g["provenance"]["style_profile_id"] == st["style_profile_id"]
    assert g["provenance"]["model_kind"] == "BASE_SPEAKER" and g["provenance"]["production_allowed"] is False
    # comparison endpoint across reference / plain / styled
    plain = ok(c.get(f"/api/voice-lab/generations/{plain['generation_id']}", headers=H))
    ref_art = ok(c.get("/api/voice-lab/references", headers=H))["references"][0]["speech_artifact_id"]
    cmpr = ok(c.get(f"/api/voice-lab/compare?artifact_ids={ref_art},{plain['artifact_id']},{g['artifact_id']}", headers=H))
    styled = cmpr["items"][2]["vs_first"]
    assert "phrase_start_error_ms" in styled and abs(styled["duration_diff_ms"]) < abs(cmpr["items"][1]["vs_first"]["duration_diff_ms"]) + 1500


def test_apply_style_unit_caps_and_timing_only():
    x = speechlike([(1000, 0, 150), (1200, 0, 150)], 220)
    y, rep = S.apply_style(x, 24000, None, target_duration_ms=int(len(x) / 24 * 2))
    assert rep["mode"] == "TIMING_ONLY" and rep["capped"] and rep["stretch_applied"] == 1.15
    feats = {"total_ms": 9000, "leading_silence_ms": 100, "trailing_silence_ms": 100, "phrases": [
        {"duration_ms": 1000, "pause_after_ms": 200, "rel_median_st": 0, "energy_rel_db": 0} for _ in range(5)]}
    y, rep = S.apply_style(x, 24000, feats)
    assert rep["mode"] == "GLOBAL_FALLBACK" and "reason" in rep


# ------------------------------------------------------------ generation / scripts
def test_generation_reuse_force_and_not_installed_model(env, monkeypatch):
    c, svc = env["c"], env["svc"]
    f, _, _ = narrator(c, "Female Narrator", "female", "Jaya")
    body = {"narrator_id": f["narrator_id"], "text": "ஒன்று. இரண்டு."}
    g1 = ok(c.post("/api/voice-lab/generations", headers=H, json=body), 202)
    g2 = ok(c.post("/api/voice-lab/generations", headers=H, json=body), 202)
    assert g2["reused"] and g2["generation_id"] == g1["generation_id"]
    g3 = ok(c.post("/api/voice-lab/generations", headers=H, json={**body, "force": True}), 202)
    assert g3["generation_id"] != g1["generation_id"]
    assert c.post("/api/voice-lab/generations", headers=H, json={**body, "language": "hi"}).status_code == 422
    # the real runtime refuses an uninstalled model instead of falling back
    from defence_studio.voice_lab.inference import ParlerBackend
    svc.backends["parler"] = ParlerBackend()
    r = c.post("/api/voice-lab/generations", headers=H, json={**body, "text": "மூன்று."})
    if r.status_code == 202:
        pytest.skip("indic-parler-tts weights are installed on this machine")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MODEL_NOT_INSTALLED"


def test_script_studio_contract_and_paragraph_regeneration(env):
    c, svc = env["c"], env["svc"]
    f, _, _ = narrator(c, "Female Narrator", "female", "Jaya")
    paras = [{"paragraph_id": "p1", "text": "முதல் பத்தி."}, {"paragraph_id": "p2", "text": "இரண்டாம் பத்தி."}]
    batch = ok(c.post("/api/voice-lab/script-generation/batch", headers=H, json={
        "script_id": "s1", "chapter_id": "c1", "narrator_id": f["narrator_id"], "paragraphs": paras}), 202)
    assert batch["queued"] == 2
    svc.run_pending()
    calls = svc.backends["parler"].calls
    paras[1]["text"] = "இரண்டாம் பத்தி திருத்தப்பட்டது."
    batch = ok(c.post("/api/voice-lab/script-generation/batch", headers=H, json={
        "script_id": "s1", "chapter_id": "c1", "narrator_id": f["narrator_id"], "paragraphs": paras}), 202)
    assert batch["reused"] == 1 and batch["queued"] == 1
    svc.run_pending()
    assert svc.backends["parler"].calls == calls + 1  # only the changed paragraph was synthesised
    res = ok(c.post("/api/voice-lab/script-generation", headers=H, json={
        "scriptText": "முதல் பத்தி.", "voiceId": f["narrator_id"], "chapterId": "c1", "paragraph_id": "p1",
        "script_id": "s1"}), 202)
    assert res["reused"] and res["status"] == "COMPLETED"
    assert res["audio"]["duration_ms"] > 500 and res["audio"]["sha256"]
    assert res["narrator"]["model_version"] == 1 and res["chapter_id"] == "c1" and res["paragraph_id"] == "p1"
    assert any("not cleared" in l for l in res["limitations"])
    assert any("base-model speaker" in l for l in res["limitations"])


class RecordingTTS(FakeTTS):
    """Test-only: FakeTTS that keeps the exact text each chunk was synthesised from."""

    def __init__(self):
        super().__init__()
        self.texts = []

    def synthesize(self, model, cfg, text, out_wav, seed):
        self.texts.append(text)
        return super().synthesize(model, cfg, text, out_wav, seed)


def test_tamil_text_preparation_feeds_model_but_keeps_approved_text(env):
    """T0-B: Voice Lab reuses services/tts_text.py. The real reviewed config files are used (no stub tables)."""
    c, svc = env["c"], env["svc"]
    svc.backends["parler"] = rec = RecordingTTS()
    f, _, _ = narrator(c, "Female Narrator", "female", "Jaya")
    approved = "Su-57 விமானம் 25% வேகமாக பறந்தது. XQZ-9 ஒரு சோதனை."
    g = ok(c.post("/api/voice-lab/generations", headers=H, json={"narrator_id": f["narrator_id"], "text": approved}), 202)
    tp = g["text_preparation"]
    assert any(r["source"] == "designation" and r["from"] == "Su-57" for r in tp["replacements"])
    assert any("XQZ-9" in w for w in tp["warnings"])  # unknown designation reported, not transliterated
    svc.run_pending()
    spoken = " ".join(rec.texts)
    assert "Su fifty seven" in spoken and "Su-57" not in spoken and "சதவீதம்" in spoken and "XQZ-9" in spoken
    done = ok(c.get(f"/api/voice-lab/generations/{g['generation_id']}", headers=H))
    assert done["status"] == "COMPLETED" and done["text"] == approved  # approved/subtitle text unchanged
    assert done["provenance"]["text_preparation"]["spoken_sha256"] == tp["spoken_sha256"]
    assert done["provenance"]["text_preparation"]["lexicon_sha256"]
    assert any("pronunciation:" in l and "XQZ-9" in l for l in svc.generation.script_result(done)["limitations"])
    # same input -> same spoken form -> reused (deterministic)
    again = ok(c.post("/api/voice-lab/generations", headers=H, json={"narrator_id": f["narrator_id"], "text": approved}), 202)
    assert again["reused"] and again["text_preparation"]["spoken"] == tp["spoken"]
    # broken Tamil (word starting with a vowel sign) is refused, not repaired
    r = c.post("/api/voice-lab/generations", headers=H, json={"narrator_id": f["narrator_id"], "text": "ா சோதனை."})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "TAMIL_TEXT_INVALID"


def test_english_generation_text_is_passed_unchanged(env):
    c, svc = env["c"], env["svc"]
    svc.backends["parler"] = rec = RecordingTTS()
    f, _, _ = narrator(c, "Female Narrator", "female", "Jaya")
    text = "The Su-57 flew 25% faster. DRDO confirmed it."
    g = ok(c.post("/api/voice-lab/generations", headers=H,
                  json={"narrator_id": f["narrator_id"], "text": text, "language": "en"}), 202)
    assert g["text_preparation"] is None
    svc.run_pending()
    assert " ".join(rec.texts) == text
    done = ok(c.get(f"/api/voice-lab/generations/{g['generation_id']}", headers=H))
    assert "text_preparation" not in done["provenance"]


def test_chunk_text_keeps_sentences():
    t = "ஒன்று. " * 80
    chunks = chunk_text(t)
    assert all(len(ch) <= 260 for ch in chunks) and "".join(chunks).replace(" ", "") == t.replace(" ", "")


# ---------------------------------------------------------------------- mixer
def _art(c, x, name="a.wav", sr=24000):
    return ok(c.post("/api/voice-lab/artifacts", headers=H, files={"file": (name, A.wav_bytes(x, sr), "audio/wav")}), 201)


def op(c, pid, o, rev=None):
    return ok(c.post(f"/api/voice-lab/projects/{pid}/ops", headers=H, json={"op": o, "expected_revision": rev}))


def test_mixer_edit_undo_redo_lock_persist_export(env):
    c = env["c"]
    tone = lambda hz, ms: (0.3 * np.sin(2 * np.pi * hz * np.arange(int(48 * ms)) / 48000)).astype(np.float32)
    a = _art(c, tone(220, 4000), sr=48000)
    b = _art(c, tone(330, 2000), sr=48000)
    assert a["duration_ms"] == 4000
    p = ok(c.post("/api/voice-lab/projects", headers=H, json={"name": "Comparison"}), 201)
    pid = p["project_id"]
    t1 = op(c, pid, {"type": "add_track", "role": "female_narrator", "name": "Female"})["result"]["track_id"]
    t2 = op(c, pid, {"type": "add_track", "role": "male_reference", "name": "Ref"})["result"]["track_id"]
    c1 = op(c, pid, {"type": "add_clip", "track_id": t1, "artifact_id": a["artifact_id"]})["result"]["clip_id"]
    r = op(c, pid, {"type": "add_clip", "track_id": t2, "artifact_id": b["artifact_id"], "start_ms": 500})
    assert r["doc"]["duration_ms"] == 4000 and r["doc"]["revision"] == 4
    stale = c.post(f"/api/voice-lab/projects/{pid}/ops", headers=H,
                   json={"op": {"type": "set_project", "name": "x"}, "expected_revision": 1})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "STALE_REVISION"
    sp = op(c, pid, {"type": "split_clip", "clip_id": c1, "at_ms": 1000})["result"]
    doc = op(c, pid, {"type": "update_clip", "clip_id": sp["right_clip_id"], "in_ms": 1500})["doc"]  # trim
    clips = doc["tracks"][0]["clips"]
    assert [(x["start_ms"], x["in_ms"], x["out_ms"]) for x in clips] == [(0, 0, 1000), (1000, 1500, 4000)]
    cut = op(c, pid, {"type": "cut_range", "track_id": t1, "t0_ms": 500, "t1_ms": 1500, "ripple": True})
    board = cut["result"]["clipboard"]
    assert sum(i["out_ms"] - i["in_ms"] for i in board) == 1000 and cut["doc"]["duration_ms"] == 2500
    doc = op(c, pid, {"type": "paste", "track_id": t1, "at_ms": 2500, "clipboard": board})["doc"]
    assert doc["duration_ms"] == 3500
    doc = op(c, pid, {"type": "insert_silence", "track_id": t1, "at_ms": 100, "duration_ms": 400})["doc"]
    assert doc["duration_ms"] == 3900
    copy_ = c.post(f"/api/voice-lab/projects/{pid}/ops", headers=H, json={"op": {"type": "copy_range", "track_id": t2,
                                                                                 "t0_ms": 0, "t1_ms": 1000}}).json()
    assert copy_["doc"]["revision"] == doc["revision"] and copy_["result"]["clipboard"][0]["in_ms"] == 0
    first = doc["tracks"][0]["clips"][0]["clip_id"]
    dup = op(c, pid, {"type": "duplicate_clip", "clip_id": first, "start_ms": 5000})["result"]["clip_id"]
    doc = op(c, pid, {"type": "update_clip", "clip_id": dup, "gain_db": -6, "fade_in_ms": 20, "fade_out_ms": 20})["doc"]
    # join: two adjacent windows of the same artifact merge without rendering
    t3 = op(c, pid, {"type": "add_track", "role": "alternative"})["result"]["track_id"]
    j1 = op(c, pid, {"type": "add_clip", "track_id": t3, "artifact_id": a["artifact_id"], "in_ms": 0, "out_ms": 1000,
                     "start_ms": 0})["result"]["clip_id"]
    j2 = op(c, pid, {"type": "add_clip", "track_id": t3, "artifact_id": a["artifact_id"], "in_ms": 1000, "out_ms": 2000,
                     "start_ms": 1000})["result"]["clip_id"]
    assert op(c, pid, {"type": "join_clips", "clip_ids": [j1, j2]})["result"]["mode"] == "MERGED_WINDOWS"
    j3 = op(c, pid, {"type": "add_clip", "track_id": t3, "artifact_id": b["artifact_id"], "start_ms": 2000})["result"]["clip_id"]
    joined = op(c, pid, {"type": "join_clips", "clip_ids": [j1, j3]})
    assert joined["result"]["mode"] == "RENDERED_JOIN"
    assert ok(c.get(f"/api/voice-lab/artifacts/{joined['result']['artifact_id']}", headers=H))["duration_ms"] == 4000
    # lock blocks edits; mute/solo still allowed
    op(c, pid, {"type": "update_track", "track_id": t2, "locked": True})
    r = c.post(f"/api/voice-lab/projects/{pid}/ops", headers=H,
               json={"op": {"type": "cut_range", "track_id": t2, "t0_ms": 0, "t1_ms": 100}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TRACK_LOCKED"
    op(c, pid, {"type": "update_track", "track_id": t2, "mute": True})
    # undo / redo
    before = ok(c.get(f"/api/voice-lab/projects/{pid}", headers=H))
    u = ok(c.post(f"/api/voice-lab/projects/{pid}/undo", headers=H))["doc"]
    assert next(t for t in u["tracks"] if t["track_id"] == t2)["mute"] is False
    rdo = ok(c.post(f"/api/voice-lab/projects/{pid}/redo", headers=H))["doc"]
    assert rdo["tracks"] == before["tracks"]
    assert c.post(f"/api/voice-lab/projects/{pid}/redo", headers=H).status_code == 409
    hist = ok(c.get(f"/api/voice-lab/projects/{pid}/history", headers=H))["operations"]
    assert len(hist) >= 18
    # source artifacts are untouched by all of the above
    assert ok(c.get(f"/api/voice-lab/artifacts/{a['artifact_id']}", headers=H))["sha256"] == a["sha256"]
    # export WAV + MP3 with normalisation and integrity
    ex = ok(c.post(f"/api/voice-lab/projects/{pid}/export", headers=H,
                   json={"format": "wav", "normalize_lufs": -18, "channels": 2}), 201)
    assert ex["integrity"]["duration_match"] and ex["artifact"]["channels"] == 2
    assert abs(ex["loudness_after"]["integrated_lufs"] - (-18)) < 1.5 or ex["peak_limited"]
    audio = c.get(ex["url"], headers=H)
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    mp3 = ok(c.post(f"/api/voice-lab/projects/{pid}/export", headers=H, json={"format": "mp3", "sample_rate": 44100}), 201)
    assert mp3["integrity"]["duration_match"] and mp3["artifact"]["format"] == "mp3"
    sel = ok(c.post(f"/api/voice-lab/projects/{pid}/export", headers=H, json={"t0_ms": 0, "t1_ms": 1000}), 201)
    assert abs(sel["artifact"]["duration_ms"] - 1000) <= 2
    wf = ok(c.get(f"/api/voice-lab/artifacts/{a['artifact_id']}/waveform?px_per_s=50", headers=H))
    assert len(wf["max"]) == 200 and max(wf["max"]) > 0.25
    pitch = ok(c.get(f"/api/voice-lab/artifacts/{a['artifact_id']}/pitch", headers=H))
    voiced = [v for v in pitch["f0_hz"] if v]
    assert voiced and abs(np.median(voiced) - 220) < 10
    saved = ok(c.get(f"/api/voice-lab/projects/{pid}", headers=H))
    env["saved"] = saved


def test_project_persists_across_restart(env):
    c = env["c"]
    tone = (0.3 * np.sin(2 * np.pi * 200 * np.arange(48000) / 48000)).astype(np.float32)
    a = _art(c, tone, sr=48000)
    pid = ok(c.post("/api/voice-lab/projects", headers=H, json={"name": "Persist"}), 201)["project_id"]
    t = op(c, pid, {"type": "add_track", "role": "female_styled"})["result"]["track_id"]
    op(c, pid, {"type": "add_clip", "track_id": t, "artifact_id": a["artifact_id"], "start_ms": 250, "gain_db": -3})
    op(c, pid, {"type": "update_track", "track_id": t, "effects": [{"type": "highpass", "freq_hz": 80},
                                                                  {"type": "eq", "freq_hz": 3000, "gain_db": 2, "q": 1}]})
    saved = ok(c.get(f"/api/voice-lab/projects/{pid}", headers=H))
    app2 = env["make"]()  # a new application instance on the same DB + storage = restart
    with TestClient(app2) as c2:
        again = ok(c2.get(f"/api/voice-lab/projects/{pid}", headers=H))
        assert again["tracks"] == saved["tracks"] and again["revision"] == saved["revision"]
        u = ok(c2.post(f"/api/voice-lab/projects/{pid}/undo", headers=H))["doc"]
        assert u["tracks"][0]["effects"] == []
        ex = ok(c2.post(f"/api/voice-lab/projects/{pid}/export", headers=H, json={"format": "flac"}), 201)
        assert ex["integrity"]["duration_match"] and ex["artifact"]["duration_ms"] == 1250


# ------------------------------------------------------------- reliability
def test_restart_reconciles_interrupted_generation(env):
    c, svc = env["c"], env["svc"]
    f, _, _ = narrator(c, "Female Narrator", "female", "Jaya")
    g = ok(c.post("/api/voice-lab/generations", headers=H, json={"narrator_id": f["narrator_id"], "text": "ஒன்று."}), 202)
    with closing(svc.ctx.conn()) as con:  # simulate: worker died mid-run, lease expired, job failed
        con.execute("UPDATE vl_generation_jobs SET status='RUNNING' WHERE generation_id=?", (g["generation_id"],))
        con.execute("UPDATE workflow_jobs SET status='FAILED' WHERE job_id=?", (g["workflow_job_id"],))
        con.commit()
    app2 = env["make"]()
    with TestClient(app2) as c2:
        g2 = ok(c2.get(f"/api/voice-lab/generations/{g['generation_id']}", headers=H))
        assert g2["status"] == "FAILED" and "interrupted" in g2["error"]
        app2.state.voice_lab.backends["parler"] = FakeTTS()
        ok(c2.post(f"/api/voice-lab/generations/{g['generation_id']}/retry", headers=H), 202)
        app2.state.voice_lab.run_pending()
        assert ok(c2.get(f"/api/voice-lab/generations/{g['generation_id']}", headers=H))["status"] == "COMPLETED"


def test_cancel_queued_generation(env):
    c, svc = env["c"], env["svc"]
    f, _, _ = narrator(c, "Female Narrator", "female", "Jaya")
    g = ok(c.post("/api/voice-lab/generations", headers=H, json={"narrator_id": f["narrator_id"], "text": "ஒன்று."}), 202)
    assert ok(c.post(f"/api/voice-lab/generations/{g['generation_id']}/cancel", headers=H))["status"] == "CANCELLED"
    assert svc.run_pending() == 0
    assert svc.backends["parler"].calls == 0


def test_disk_guard_missing_file_and_storage_report(env, monkeypatch):
    c, svc = env["c"], env["svc"]
    x = speechlike([(1500, 0, 300)], 200)
    a = _art(c, x)
    monkeypatch.setenv("DEFENCE_STUDIO_VOICE_LAB_RESERVE_BYTES", str(10 ** 18))
    r = c.post("/api/voice-lab/artifacts", headers=H, files={"file": ("b.wav", wav(x), "audio/wav")})
    assert r.status_code == 507 and r.json()["detail"]["code"] == "INSUFFICIENT_DISK"
    monkeypatch.setenv("DEFENCE_STUDIO_VOICE_LAB_RESERVE_BYTES", "0")
    rep = ok(c.get("/api/voice-lab/storage", headers=H))
    assert rep["by_kind"]["PROJECT_UPLOAD"]["count"] == 1 and rep["missing_files"] == []
    assert all(v["retention"] == "PRESERVE" for v in rep["by_kind"].values())
    (env["store"] / a["rel_path"]).unlink()
    r = c.get(f"/api/voice-lab/artifacts/{a['artifact_id']}/audio", headers=H)
    assert r.status_code == 410 and r.json()["detail"]["code"] == "ARTIFACT_FILE_MISSING"
    assert ok(c.get("/api/voice-lab/storage", headers=H))["missing_files"][0]["artifact_id"] == a["artifact_id"]
    assert ok(c.get(f"/api/voice-lab/artifacts/{a['artifact_id']}", headers=H))["file_present"] is False
    assert a["rel_path"] in svc.protected_paths()


def test_reviews_bound_to_model_version(env):
    c, svc = env["c"], env["svc"]
    f, _, fv = narrator(c, "Female Narrator", "female", "Jaya")
    g = ok(c.post("/api/voice-lab/generations", headers=H, json={"narrator_id": f["narrator_id"], "text": "ஒன்று."}), 202)
    svc.run_pending()
    g = ok(c.get(f"/api/voice-lab/generations/{g['generation_id']}", headers=H))
    rv = ok(c.post("/api/voice-lab/reviews", headers=H, json={"artifact_id": g["artifact_id"], "generation_id": g["generation_id"],
                                                             "ratings": {"naturalness": 3, "identity": 4},
                                                             "notes": "listened on headphones"}), 201)
    assert rv["model_version_id"] == fv["model_version_id"]
    assert c.post("/api/voice-lab/reviews", headers=H, json={"artifact_id": g["artifact_id"],
                                                             "ratings": {"x": 9}}).status_code == 422
    assert len(ok(c.get(f"/api/voice-lab/reviews?model_version_id={fv['model_version_id']}", headers=H))["reviews"]) == 1


def test_real_hardware_detection_reports_measured_values(tmp_path):
    hw = hardware.detect(tmp_path, refresh=True)
    assert hw["cpu"]["threads"] and hw["ram_bytes"] and hw["disks"]
    assert isinstance(hw["gpus"], list)
    cap = hardware.training_capability(hw, {"training": {"supported": True, "requires": "cuda", "min_vram_gb": 24}})
    assert cap["supported"] == bool(hw["cuda_usable"] and any(
        (g.get("vram_bytes") or 0) >= 24 * 1024 ** 3 for g in hw["gpus"] if g["vendor"] == "NVIDIA"))
