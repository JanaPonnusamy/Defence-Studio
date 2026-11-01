"""Training orchestration with hardware/licence/dataset gating (VL-4).

A training job row is created ONLY after every preflight check passes. On hardware
that cannot train (e.g. no CUDA GPU), ``start`` returns 409 with the measured
blockers and nothing is launched. Jobs run on the shared workflow queue (stage
``vl_training``), so leases, crash recovery and retry come from WorkerService.

Trainer adapters implement ``run(job, manifest_path, workdir, resume_from, log, cancel,
on_checkpoint, on_metrics) -> {"final_checkpoint": path, "metrics": {...}}``.
The production Parler adapter only builds and runs the pinned upstream training
command; it has never been executed on compatible hardware (stated in ``describe``).
"""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import closing
from pathlib import Path

from defence_studio.config import PROJECT_ROOT
from defence_studio.voice_lab import hardware
from defence_studio.voice_lab.common import VoiceLabError, canon, loads, new_id, now, sha256_file, venv_python
from defence_studio.voice_lab.context import Ctx
from defence_studio.voice_lab.datasets import Datasets
from defence_studio.voice_lab.narrators import Narrators

ACTIVE = ("QUEUED", "RUNNING", "PAUSE_REQUESTED")
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


def max_concurrent() -> int:
    return int(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_MAX_TRAINING_JOBS", 1))


class ParlerTrainer:
    runtime = "parler"
    supports_pause = True
    supports_resume = True

    def describe(self, model):
        commit = model["code"]["commit"]
        src = PROJECT_ROOT / "third_party" / "voice_lab" / f"parler-tts@{commit[:12]}"
        return {"adapter": "parler-finetune", "source_checkout": str(src), "source_present": src.is_dir(),
                "verification": "UNVERIFIED: never executed on compatible hardware; requires CUDA",
                "entrypoint": model["training"].get("entrypoint")}

    def run(self, job, manifest_path, workdir, resume_from, log, cancel, on_checkpoint, on_metrics):
        model = job["model"]
        info = self.describe(model)
        if not info["source_present"]:
            raise RuntimeError(f"Parler-TTS training source missing: {info['source_checkout']}")
        cfg = {"model_name_or_path": str(job["base_model_dir"]), "output_dir": str(workdir),
               "voice_lab_manifest": str(manifest_path), "resume_from_checkpoint": resume_from,
               **(job["config"].get("hyperparameters") or {})}
        cfg_path = Path(workdir) / "train_config.json"
        cfg_path.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
        cmd = [str(venv_python()), str(Path(info["source_checkout"]) / info["entrypoint"]), str(cfg_path)]
        log.write(f"$ {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=info["source_checkout"])
        while proc.poll() is None:
            if cancel.wait(5):
                proc.terminate()
                proc.wait(60)
                raise RuntimeError("cancelled")
            for ck in sorted(Path(workdir).glob("checkpoint-*")):
                on_checkpoint(ck)
        if proc.returncode != 0:
            raise RuntimeError(f"training process exited with {proc.returncode}")
        return {"final_checkpoint": str(workdir), "metrics": {}}


class Training:
    def __init__(self, ctx: Ctx, narrators: Narrators, datasets: Datasets, adapters: dict | None = None,
                 hardware_probe=None):
        self.ctx, self.narr, self.ds = ctx, narrators, datasets
        self.adapters = adapters or {"parler": ParlerTrainer()}
        self.hardware_probe = hardware_probe or (lambda: hardware.detect(ctx.storage_root))

    def preflight(self, owner_id, *, narrator_id, dataset_version_id, model_id, target="local"):
        n = self.narr.get(narrator_id, owner_id)
        model = self.ctx.model(model_id)
        checks, blockers = [], []
        hw = self.hardware_probe()
        cap = hardware.training_capability(hw, model, target=target)
        blockers += cap["blockers"]
        adapter = self.adapters.get(model.get("runtime") or "")
        if not adapter:
            blockers.append({"code": "NO_TRAINER", "detail": f"no training adapter for {model_id}"})
        lic = model.get("commercial_status")
        if lic in ("NOT_ELIGIBLE",):
            blockers.append({"code": "LICENCE_NOT_ELIGIBLE", "detail": f"{model_id} is {lic}"})
        try:
            v = self.ds.version(dataset_version_id, owner_id)
            if v["narrator_id"] != narrator_id:
                blockers.append({"code": "IDENTITY_MISMATCH",
                                 "detail": "dataset version belongs to another narrator; identities are never mixed"})
            if v["status"] != "APPROVED":
                blockers.append({"code": "DATASET_NOT_APPROVED", "detail": f"dataset version is {v['status']}"})
            ds = self.ds._row(v["dataset_id"], owner_id)
            try:
                self.narr.require_consent(ds["consent_id"], owner_id, "TRAINING")
            except VoiceLabError as e:
                blockers.append(e.detail if isinstance(e.detail, dict) else {"code": "CONSENT", "detail": e.detail})
        except VoiceLabError as e:
            blockers.append({"code": "DATASET_VERSION", "detail": str(e.detail)})
        if n["status"] != "ACTIVE":
            blockers.append({"code": "NARRATOR_ARCHIVED", "detail": "narrator is archived"})
        with closing(self.ctx.conn()) as c:
            running = c.execute(f"SELECT COUNT(*) FROM vl_training_jobs WHERE status IN ({','.join('?' * len(ACTIVE))})",
                                ACTIVE).fetchone()[0]
        if running >= max_concurrent():
            blockers.append({"code": "CONCURRENCY_LIMIT", "detail": f"{running} training job(s) active (limit {max_concurrent()})"})
        return {"ready": not blockers, "blockers": blockers, "capability": cap,
                "adapter": adapter.describe(model) if adapter and hasattr(adapter, "describe") else None,
                "hardware": {k: hw.get(k) for k in ("cpu", "ram_bytes", "gpus", "cuda_usable", "rocm_usable",
                                                    "cloud_training_enabled", "measured_at")},
                "licence": {"commercial_status": lic, "production_allowed": bool(model.get("production_allowed"))}}

    def start(self, owner_id, idem, *, narrator_id, dataset_version_id, model_id, target="local", config=None,
              resume_from_job=None, enqueue):
        pf = self.preflight(owner_id, narrator_id=narrator_id, dataset_version_id=dataset_version_id,
                            model_id=model_id, target=target)
        if not pf["ready"]:
            raise VoiceLabError(409, {"code": "TRAINING_BLOCKED", "blockers": pf["blockers"], "preflight": pf})
        resume = None
        if resume_from_job:
            prev = self.get(resume_from_job, owner_id)
            if prev["narrator_id"] != narrator_id:
                raise VoiceLabError(409, {"code": "IDENTITY_MISMATCH", "detail": "cannot resume another narrator's job"})
            resume = (prev.get("metrics") or {}).get("last_checkpoint")
            if not resume:
                raise VoiceLabError(409, "previous job has no checkpoint to resume from")
        tid, ts = new_id("train"), now()
        workdir_rel = f"voice_lab/training/{tid}"
        (self.ctx.storage_root / workdir_rel).mkdir(parents=True, exist_ok=True)
        with self.ctx.tx() as c:
            c.execute("INSERT INTO vl_training_jobs VALUES(?,?,?,?,?,?,?,'QUEUED',NULL,?,?,?,NULL,NULL,NULL,?,?)",
                      (tid, owner_id, narrator_id, dataset_version_id, model_id, target, canon(config or {}), resume,
                       f"{workdir_rel}/train.log", f"{workdir_rel}/checkpoints", ts, ts))
        wf = enqueue(owner_id, idem, "vl_training", tid, f"Voice Lab training {narrator_id}")
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_training_jobs SET workflow_job_id=? WHERE training_job_id=?", (wf, tid))
        return self.get(tid, owner_id)

    def get(self, tid, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT * FROM vl_training_jobs WHERE training_job_id=? AND owner_id=?", (tid, owner_id)).fetchone()
        if not r:
            raise VoiceLabError(404, "training job not found")
        d = dict(r)
        d["config"] = loads(d.pop("config_json"), {})
        d["metrics"] = loads(d.pop("metrics_json"), None)
        return d

    def list(self, owner_id, narrator_id=None):
        q, a = "SELECT training_job_id FROM vl_training_jobs WHERE owner_id=?", [owner_id]
        if narrator_id:
            q += " AND narrator_id=?"
            a.append(narrator_id)
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute(q + " ORDER BY created_at DESC", a)]
        return [self.get(i, owner_id) for i in ids]

    def log_tail(self, tid, owner_id, max_bytes=20000):
        j = self.get(tid, owner_id)
        p = self.ctx.storage_root / (j["log_rel_path"] or "")
        if not j["log_rel_path"] or not p.is_file():
            return {"training_job_id": tid, "log": "", "bytes": 0}
        data = p.read_bytes()
        return {"training_job_id": tid, "log": data[-max_bytes:].decode("utf-8", "replace"), "bytes": len(data)}

    def request(self, tid, owner_id, action, cancel_workflow):
        j = self.get(tid, owner_id)
        if j["status"] in TERMINAL or j["status"] == "PAUSED":
            raise VoiceLabError(409, f"job is {j['status']}")
        if action == "pause":
            adapter = self.adapters.get(self.ctx.model(j["model_id"]).get("runtime") or "")
            if not getattr(adapter, "supports_pause", False):
                raise VoiceLabError(409, "pause is not supported by this trainer")
            new = "PAUSE_REQUESTED"
        else:
            new = "CANCEL_REQUESTED"
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_training_jobs SET status=?, updated_at=? WHERE training_job_id=?", (new, now(), tid))
        if j["workflow_job_id"] and j["status"] == "QUEUED" and action == "cancel":
            cancel_workflow(j["workflow_job_id"], owner_id)
            self._set(tid, status="CANCELLED")
        return self.get(tid, owner_id)

    def _set(self, tid, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.ctx.tx() as c:
            c.execute(f"UPDATE vl_training_jobs SET {sets}, updated_at=? WHERE training_job_id=?",
                      (*[canon(v) if isinstance(v, (dict, list)) else v for v in cols.values()], now(), tid))

    def _status(self, tid):
        with closing(self.ctx.conn()) as c:
            return c.execute("SELECT status FROM vl_training_jobs WHERE training_job_id=?", (tid,)).fetchone()[0]

    # ----------------------------------------------------------------- handler
    def run_job(self, tid, owner_id, cancel_event):
        import threading
        j = self.get(tid, owner_id)
        if j["status"] in TERMINAL:
            return
        model = self.ctx.model(j["model_id"])
        adapter = self.adapters[model["runtime"]]
        # Re-check the gate at execution time: hardware may have changed since queueing.
        pf = self.preflight(owner_id, narrator_id=j["narrator_id"], dataset_version_id=j["dataset_version_id"],
                            model_id=j["model_id"], target=j["target"])
        fatal = [b for b in pf["blockers"] if b["code"] != "CONCURRENCY_LIMIT"]
        if fatal:
            self._set(tid, status="FAILED", error=canon({"blockers": fatal}))
            return
        v = self.ds.version(j["dataset_version_id"], owner_id)
        manifest = self.ctx.storage_root / v["manifest_rel_path"]
        if sha256_file(manifest) != v["manifest_sha256"]:
            self._set(tid, status="FAILED", error="dataset manifest integrity check failed")
            return
        workdir = self.ctx.storage_root / j["checkpoint_rel_dir"]
        workdir.mkdir(parents=True, exist_ok=True)
        metrics = j["metrics"] or {"checkpoints": [], "history": []}
        self._set(tid, status="RUNNING", metrics_json=metrics)
        stop = threading.Event()

        def watch():
            while not stop.wait(1):
                st = self._status(tid)
                if cancel_event.is_set() or st in ("CANCEL_REQUESTED", "PAUSE_REQUESTED"):
                    stop.set()
        threading.Thread(target=watch, daemon=True).start()

        def on_checkpoint(path):
            p = str(Path(path))
            if p not in [c["path"] for c in metrics["checkpoints"]]:
                metrics["checkpoints"].append({"path": p, "at": now()})
                metrics["last_checkpoint"] = p
                self._set(tid, metrics_json=metrics)

        def on_metrics(m):
            metrics["history"].append({"at": now(), **m})
            self._set(tid, metrics_json=metrics)

        base_dir = None
        from defence_studio.voice_lab.inference import model_dir
        base_dir = model_dir(model)
        job = {**j, "model": model, "base_model_dir": base_dir}
        log_path = self.ctx.storage_root / j["log_rel_path"]
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"[{now()}] start training {tid} narrator={j['narrator_id']} dataset={j['dataset_version_id']}\n")
            try:
                res = adapter.run(job, manifest, workdir, j["resume_from"], log, stop, on_checkpoint, on_metrics)
            except Exception as exc:
                st = self._status(tid)
                if st == "PAUSE_REQUESTED":
                    self._set(tid, status="PAUSED", metrics_json=metrics)
                    log.write(f"[{now()}] paused at {metrics.get('last_checkpoint')}\n")
                    return
                if st == "CANCEL_REQUESTED" or cancel_event.is_set():
                    self._set(tid, status="CANCELLED", metrics_json=metrics)
                    return
                self._set(tid, status="FAILED", error=f"{type(exc).__name__}: {exc}"[:2000], metrics_json=metrics)
                log.write(f"[{now()}] failed: {exc}\n")
                raise
            finally:
                stop.set()
        final = Path(res["final_checkpoint"])
        files = sorted(final.rglob("*")) if final.is_dir() else [final]
        import hashlib
        h = hashlib.sha256()
        for f in files:
            if f.is_file():
                h.update(f.relative_to(final if final.is_dir() else final.parent).as_posix().encode())
                h.update(sha256_file(f).encode())
        ck_sha = h.hexdigest()
        metrics.update(res.get("metrics") or {})
        n = self.narr.get(j["narrator_id"], owner_id)
        with self.ctx.tx() as c:
            mvid = self.narr.add_trained_version(
                c, j["narrator_id"], model_id=j["model_id"],
                checkpoint_rel_path=final.relative_to(self.ctx.storage_root).as_posix(), checkpoint_sha256=ck_sha,
                parent_version_id=n["active_model_version_id"], training_job_id=tid,
                cfg={"dataset_version_id": j["dataset_version_id"], "training_config": j["config"],
                     "base_weights_revision": model["weights"]["revision"]}, metrics=metrics)
        self._set(tid, status="COMPLETED", metrics_json=metrics, result_model_version_id=mvid)
