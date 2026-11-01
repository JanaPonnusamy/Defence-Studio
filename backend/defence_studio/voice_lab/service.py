"""Voice Lab facade: wires components, owns the queue integration and the in-process worker.

Jobs use the shared ``workflow_jobs`` queue with Voice Lab stages
(``vl_generation``, ``vl_reference_analysis``, ``vl_dataset_segment``, ``vl_training``);
WorkerService provides leases, crash recovery and retry. ``vl_queue`` maps a workflow
job to the Voice Lab row it processes. ``reconcile`` repairs Voice Lab rows after a
restart (a row left RUNNING whose workflow job failed or was cancelled).
"""
from __future__ import annotations

import hashlib
import os
import threading
import uuid
from contextlib import closing
from pathlib import Path

from defence_studio.voice_lab import hardware
from defence_studio.voice_lab.common import VoiceLabError, canon, now
from defence_studio.voice_lab.context import RETENTION, Ctx
from defence_studio.voice_lab.datasets import Datasets
from defence_studio.voice_lab.generation import Generation
from defence_studio.voice_lab.inference import ParlerBackend, install_status
from defence_studio.voice_lab.narrators import Narrators
from defence_studio.voice_lab.projects import Projects
from defence_studio.voice_lab.references import References
from defence_studio.voice_lab.training import Training

SCHEMA_PREFIX = "defence-studio.voice-lab"
STAGES = ("vl_generation", "vl_reference_analysis", "vl_dataset_segment", "vl_training")


class VoiceLabService:
    def __init__(self, database_file, storage_root, *, backends=None, trainers=None, hardware_probe=None,
                 start_worker: bool | None = None):
        self.ctx = Ctx(Path(database_file), Path(storage_root))
        self.narrators = Narrators(self.ctx)
        self.datasets = Datasets(self.ctx, self.narrators)
        self.references = References(self.ctx)
        self.backends = backends if backends is not None else {"parler": ParlerBackend()}
        self.generation = Generation(self.ctx, self.narrators, self.references, self.backends)
        self.training = Training(self.ctx, self.narrators, self.datasets, adapters=trainers,
                                 hardware_probe=hardware_probe)
        self.projects = Projects(self.ctx)
        self._hardware_probe = hardware_probe
        self._worker = None
        self._worker_lock = threading.Lock()
        self.start_worker = (os.environ.get("DEFENCE_STUDIO_VOICE_LAB_WORKER", "1") == "1"
                             if start_worker is None else start_worker)

    # ------------------------------------------------------------------ queue
    def enqueue(self, owner_id, idem, stage, ref_id, topic):
        if stage not in STAGES:
            raise ValueError(stage)
        key = idem or f"{stage}:{ref_id}"
        schema = f"{SCHEMA_PREFIX}.{stage}.v1"
        digest = hashlib.sha256(canon({"schema": schema, "ref": ref_id}).encode()).hexdigest()
        job_id, ts = str(uuid.uuid4()), now()
        with self.ctx.tx() as c:
            ex = c.execute("SELECT job_id, request_hash FROM workflow_jobs WHERE owner_id=? AND idempotency_key=?",
                           (owner_id, key)).fetchone()
            if ex:
                if ex["request_hash"] != digest:
                    raise VoiceLabError(409, "Idempotency-Key was already used for a different request")
                return ex["job_id"]
            c.execute("INSERT INTO workflow_jobs(job_id, owner_id, idempotency_key, request_schema, request_hash, topic, "
                      "language, status, created_at, updated_at, queued_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (job_id, owner_id, key, schema, digest, topic[:200], "und", "QUEUED", ts, ts, ts))
            c.execute("INSERT INTO workflow_stages(job_id, stage_key, ordinal, status, version, created_at, updated_at) "
                      "VALUES(?,?,0,'PENDING',1,?,?)", (job_id, stage, ts, ts))
            c.execute("INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                      "VALUES(?,?,'','PENDING',1,?)", (job_id, stage, ts))
            c.execute("INSERT INTO vl_queue VALUES(?,?,?,?,?)", (job_id, owner_id, stage, ref_id, ts))
        self.ensure_worker()
        return job_id

    def cancel_workflow(self, job_id, owner_id):
        ts = now()
        with self.ctx.tx() as c:
            j = c.execute("SELECT status FROM workflow_jobs WHERE job_id=? AND owner_id=?", (job_id, owner_id)).fetchone()
            if not j or j["status"] not in ("QUEUED", "RUNNING"):
                return False
            c.execute("UPDATE worker_claims SET status='FAILED', finished_at=?, failure_detail='CANCELLED' "
                      "WHERE job_id=? AND status='ACTIVE'", (ts, job_id))
            c.execute("UPDATE workflow_stages SET status='FAILED', version=version+1, updated_at=?, error='Cancelled by user' "
                      "WHERE job_id=? AND status IN ('PENDING','RUNNING','FAILED')", (ts, job_id))
            c.execute("UPDATE workflow_jobs SET status='CANCELLED', updated_at=? WHERE job_id=?", (ts, job_id))
        return True

    def _wf_cancelled(self, job_id, cancel):
        if cancel.is_set():
            return True
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (job_id,)).fetchone()
        return r is None or r["status"] == "CANCELLED"

    def _ref(self, context):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT ref_id FROM vl_queue WHERE workflow_job_id=?", (context.job_id,)).fetchone()
        if not r:
            raise RuntimeError("Voice Lab queue entry missing")
        return r["ref_id"]

    def _h_generation(self, context, cancel):
        gid = self._ref(context)
        try:
            self.generation.run(gid, context.owner_id, lambda: self._wf_cancelled(context.job_id, cancel))
        except Exception as exc:
            st = "CANCELLED" if self._wf_cancelled(context.job_id, cancel) else "FAILED"
            self.generation._set(gid, status=st, error=(exc.detail if isinstance(exc, VoiceLabError) else str(exc)),
                                 progress_note=st.lower())
            raise
        return []

    def _h_reference(self, context, cancel):
        self.references.analyse(context.owner_id, self._ref(context), lambda: self._wf_cancelled(context.job_id, cancel))
        return []

    def _h_segment(self, context, cancel):
        fid = self._ref(context)
        try:
            self.datasets.segment_file(context.owner_id, fid, lambda: self._wf_cancelled(context.job_id, cancel))
        except Exception as exc:
            with self.ctx.tx() as c:
                c.execute("UPDATE vl_dataset_files SET status='FAILED' WHERE file_id=?", (fid,))
            raise
        return []

    def _h_training(self, context, cancel):
        self.training.run_job(self._ref(context), context.owner_id, cancel)
        return []

    def handlers(self):
        return {"vl_generation": self._h_generation, "vl_reference_analysis": self._h_reference,
                "vl_dataset_segment": self._h_segment, "vl_training": self._h_training}

    def ensure_worker(self):
        if not self.start_worker:
            return
        with self._worker_lock:
            if self._worker is not None:
                return
            from defence_studio.services.worker import Worker
            h = self.handlers()
            self._worker = Worker(self.ctx.db_path, f"voice-lab-{uuid.uuid4()}", h, lease_seconds=120,
                                  retry_safe_stages=set(h))
            threading.Thread(target=self._worker.run_forever, daemon=True, name="voice-lab-worker").start()

    def run_pending(self, limit=20) -> int:
        """Process queued Voice Lab stages synchronously (tests / CLI worker)."""
        from defence_studio.services.worker import Worker
        h = self.handlers()
        w = Worker(self.ctx.db_path, f"voice-lab-sync-{uuid.uuid4()}", h, lease_seconds=120, retry_safe_stages=set(h))
        n = 0
        while n < limit and w.run_once():
            n += 1
        return n

    def shutdown(self):
        if self._worker:
            self._worker.shutdown()
        for b in self.backends.values():
            if hasattr(b, "shutdown"):
                b.shutdown()

    def reconcile(self) -> dict:
        """After a restart: Voice Lab rows that claim to be active but whose workflow job is terminal."""
        fixed = {"generation": 0, "reference": 0, "dataset_file": 0, "training": 0}
        with self.ctx.tx() as c:
            rows = c.execute("SELECT q.stage, q.ref_id, j.status FROM vl_queue q JOIN workflow_jobs j "
                             "ON j.job_id=q.workflow_job_id WHERE j.status IN ('FAILED','CANCELLED')").fetchall()
            for r in rows:
                st = "CANCELLED" if r["status"] == "CANCELLED" else "FAILED"
                note = "interrupted: workflow job " + r["status"].lower()
                if r["stage"] == "vl_generation":
                    fixed["generation"] += c.execute(
                        "UPDATE vl_generation_jobs SET status=?, error=COALESCE(error, ?), updated_at=? "
                        "WHERE generation_id=? AND status IN ('QUEUED','RUNNING')", (st, note, now(), r["ref_id"])).rowcount
                elif r["stage"] == "vl_reference_analysis":
                    fixed["reference"] += c.execute(
                        "UPDATE vl_reference_media SET status='FAILED', error=COALESCE(error, ?), updated_at=? "
                        "WHERE reference_id=? AND status IN ('QUEUED','RUNNING')", (note, now(), r["ref_id"])).rowcount
                elif r["stage"] == "vl_dataset_segment":
                    fixed["dataset_file"] += c.execute(
                        "UPDATE vl_dataset_files SET status='FAILED' WHERE file_id=? AND status IN ('QUEUED','RUNNING')",
                        (r["ref_id"],)).rowcount
                elif r["stage"] == "vl_training":
                    fixed["training"] += c.execute(
                        "UPDATE vl_training_jobs SET status=?, error=COALESCE(error, ?), updated_at=? WHERE "
                        "training_job_id=? AND status IN ('QUEUED','RUNNING','PAUSE_REQUESTED','CANCEL_REQUESTED')",
                        ("CANCELLED" if st == "CANCELLED" else "FAILED", note, now(), r["ref_id"])).rowcount
        return fixed

    def retry(self, owner_id, kind, ref_id):
        """Re-queue a FAILED/CANCELLED item as a new workflow job (no data is discarded)."""
        if kind == "generation":
            g = self.generation.get(ref_id, owner_id)
            if g["status"] not in ("FAILED", "CANCELLED"):
                raise VoiceLabError(409, f"generation is {g['status']}")
            self.generation._set(ref_id, status="QUEUED", error=None)
            wf = self.enqueue(owner_id, f"vl-gen-{ref_id}-retry-{uuid.uuid4().hex[:8]}", "vl_generation", ref_id,
                              "Voice Lab generation retry")
            self.generation._set(ref_id, workflow_job_id=wf)
            return self.generation.get(ref_id, owner_id)
        if kind == "reference":
            r = self.references.get(ref_id, owner_id)
            if r["status"] not in ("FAILED",):
                raise VoiceLabError(409, f"reference is {r['status']}")
            self.references._set(ref_id, status="QUEUED")
            self.enqueue(owner_id, f"vl-ref-{ref_id}-retry-{uuid.uuid4().hex[:8]}", "vl_reference_analysis", ref_id,
                         "Voice Lab reference analysis retry")
            return self.references.get(ref_id, owner_id)
        raise VoiceLabError(422, "kind must be generation or reference")

    # ------------------------------------------------------------ composites
    def system(self, refresh=False):
        hw = (self._hardware_probe() if self._hardware_probe else hardware.detect(self.ctx.storage_root, refresh=refresh))
        caps = {mid: hardware.training_capability(hw, m) for mid, m in self.ctx.catalogue().items()
                if (m.get("training") or {}).get("supported")}
        blockers = sorted({b["code"] for c in caps.values() for b in c["blockers"]})
        return {**hw, "training_supported": any(c["supported"] for c in caps.values()),
                "training_blockers": blockers, "training_by_model": caps,
                "inference": {"device": "cpu" if not hw.get("cuda_usable") else "cuda",
                              "note": "CPU inference; see /api/voice-lab/hardware for the DirectML execution report"}}

    def models(self):
        out = []
        for mid, m in self.ctx.catalogue().items():
            st = install_status(m) if m.get("runtime") else {"installed": False, "reason": "no integrated runtime"}
            out.append({"model_id": mid, "display_name": m["display_name"], "runtime": m.get("runtime"),
                        "languages": m.get("languages"), "code_repo": m["code"]["repo"], "commit": m["code"]["commit"],
                        "code_licence": m["code"]["licence"], "weights_repo": m["weights"]["repo"],
                        "weights_rev": m["weights"]["revision"], "weight_licence": m["weights"]["licence"],
                        "gated": m["weights"].get("gated"), "data_licence": m["training_data"]["licence"],
                        "data_provenance": m["training_data"]["provenance"], "access_terms": m["access_terms"],
                        "attribution": m["attribution"], "commercial": m["commercial_status"],
                        "commercial_blockers": m.get("commercial_blockers", []),
                        "production_allowed": bool(m.get("production_allowed")), "install": st,
                        "trainable": bool((m.get("training") or {}).get("supported"))})
        return out

    def storage_report(self, owner_id):
        with closing(self.ctx.conn()) as c:
            rows = c.execute("SELECT kind, COUNT(*) n, COALESCE(SUM(bytes),0) b FROM vl_audio_artifacts WHERE owner_id=? "
                             "GROUP BY kind", (owner_id,)).fetchall()
            arts = c.execute("SELECT artifact_id, rel_path, kind FROM vl_audio_artifacts WHERE owner_id=?", (owner_id,)).fetchall()
        missing = [dict(a) for a in arts if not (self.ctx.storage_root / a["rel_path"]).is_file()]
        tmp_files = [p for p in self.ctx.tmp.rglob("*") if p.is_file()]
        models_bytes = 0
        from defence_studio.voice_lab.common import models_root
        if models_root().is_dir():
            models_bytes = sum(p.stat().st_size for p in models_root().rglob("*") if p.is_file())
        return {"measured_at": now(), "root": "voice_lab/",
                "by_kind": {r["kind"]: {"count": r["n"], "bytes": r["b"], "retention": "PRESERVE",
                                        "meaning": RETENTION.get(r["kind"])} for r in rows},
                "registered_bytes": sum(r["b"] for r in rows),
                "temp_intermediates": {"files": len(tmp_files), "bytes": sum(p.stat().st_size for p in tmp_files),
                                       "path": "voice_lab/tmp/", "policy": "Voice Lab deletes only its own per-job "
                                       "scratch files on job exit; leftovers are reported, never auto-deleted"},
                "missing_files": missing,
                "model_store_bytes": models_bytes,
                "storage_manager": {"classification": "voice_lab/ files are UNCLASSIFIED to the Storage Manager, which "
                                    "always preserves them; no Voice Lab file can be selected for cleanup",
                                    "protected": "all registered artifacts (originals, segments, manifests, checkpoints, "
                                                 "generated, projects, exports)"}}

    def protected_paths(self) -> list[str]:
        """Storage-relative paths Voice Lab requires (for Storage Manager integration)."""
        with closing(self.ctx.conn()) as c:
            return [r[0] for r in c.execute("SELECT rel_path FROM vl_audio_artifacts")]
