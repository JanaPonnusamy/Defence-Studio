"""Voice Analyzer / Voice Generation jobs on the existing ``workflow_jobs`` queue.

One workflow job == one voice job with a single stage (``voice_analysis`` or
``voice_generation``). Claiming, leases, retries and restart recovery are the
existing WorkerService behaviour; nothing here is a second queue.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.database import connect
from defence_studio.services import voice_engine as ve
from defence_studio.services.jobs import (IdempotencyConflict, InvalidTransition, JobNotFound,
                                          JobValidationError)

ANALYSIS_SCHEMA = "defence-studio.voice-analysis.v1"
GENERATION_SCHEMA = "defence-studio.voice-generation.v1"
STAGE_ANALYSIS, STAGE_GENERATION = "voice_analysis", "voice_generation"
KINDS = {"ANALYSIS": (ANALYSIS_SCHEMA, STAGE_ANALYSIS), "GENERATION": (GENERATION_SCHEMA, STAGE_GENERATION)}
PROVENANCE = {"AUTHORIZED", "OWNED", "LICENSED"}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class VoiceNotFound(JobNotFound):
    pass


class VoiceService:
    def __init__(self, database_file, storage_root):
        self.database_file = Path(database_file)
        self.storage_root = Path(storage_root)
        self.voice_root = self.storage_root / "voice"
        self.upload_dir = self.voice_root / "uploads"
        self.audio_dir = self.voice_root / "audio"
        self.max_upload_bytes = int(os.environ.get("DEFENCE_STUDIO_VOICE_MAX_UPLOAD_BYTES", 200 * 1024 * 1024))
        self.retain_reference = os.environ.get("DEFENCE_STUDIO_VOICE_RETAIN_REFERENCE", "") == "1"
        for d in (self.upload_dir, self.audio_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ jobs
    def _create_job(self, owner_id, idem, kind, payload, language, topic, temp_path=None, retry_of=None):
        if not idem or len(idem) > 200:
            raise JobValidationError("Idempotency-Key is required (1-200 chars)")
        schema, stage = KINDS[kind]
        digest = hashlib.sha256(_canon({"schema": schema, **payload}).encode()).hexdigest()
        job_id, now = str(uuid.uuid4()), _now()
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                ex = c.execute("SELECT job_id, request_hash FROM workflow_jobs WHERE owner_id=? AND idempotency_key=?",
                               (owner_id, idem)).fetchone()
                if ex:
                    if ex["request_hash"] != digest:
                        raise IdempotencyConflict("Idempotency key was already used with a different request")
                    c.commit()
                    return ex["job_id"], False
                c.execute("INSERT INTO workflow_jobs(job_id, owner_id, idempotency_key, request_schema, request_hash, "
                          "topic, language, status, created_at, updated_at, queued_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (job_id, owner_id, idem, schema, digest, topic[:200], language, "QUEUED", now, now, now))
                c.execute("INSERT INTO workflow_stages(job_id, stage_key, ordinal, status, version, created_at, updated_at) "
                          "VALUES(?,?,0,'PENDING',1,?,?)", (job_id, stage, now, now))
                c.execute("INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                          "VALUES(?,?,'','PENDING',1,?)", (job_id, stage, now))
                c.execute("INSERT INTO voice_job_requests(job_id, owner_id, kind, payload_json, payload_sha256, retry_of, "
                          "temp_path, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (job_id, owner_id, kind, _canon(payload), digest, retry_of, temp_path, now, now))
                c.commit()
            except Exception:
                if c.in_transaction:
                    c.rollback()
                raise
        return job_id, True

    def create_analysis(self, owner_id, idem, *, temp_path, original_name, size_bytes, sha256, profile_name,
                        language, authorization_ref, provenance_state="AUTHORIZED", transcript=None,
                        base_voice_style_id=None):
        if not (profile_name or "").strip():
            raise JobValidationError("profile_name is required")
        if not (authorization_ref or "").strip():
            raise JobValidationError("authorization_ref is required: confirm you are authorized to analyze this audio")
        if provenance_state not in PROVENANCE:
            raise JobValidationError(f"provenance_state must be one of {sorted(PROVENANCE)}")
        if not (language or "").strip():
            raise JobValidationError("language is required")
        if base_voice_style_id:
            self.profile(base_voice_style_id, owner_id)
        payload = {"profile_name": profile_name.strip(), "language": language.strip(),
                   "authorization_ref": authorization_ref.strip(), "provenance_state": provenance_state,
                   "transcript": transcript or None, "base_voice_style_id": base_voice_style_id,
                   "source": {"original_name": os.path.basename(original_name or "upload")[:200],
                              "size_bytes": size_bytes, "sha256": sha256}}
        job_id, created = self._create_job(owner_id, idem, "ANALYSIS", payload, language,
                                           f"Voice analysis: {profile_name.strip()}", temp_path=str(temp_path))
        if not created:                   # duplicate submission: drop the redundant upload copy
            try:
                Path(temp_path).unlink()
            except OSError:
                pass
        return self.get_job(job_id, owner_id)

    def create_generation(self, owner_id, idem, *, script, direction):
        ve.ensure_path()
        from voice_generation import DirectionError, validate_direction
        if not isinstance(script, str) or not script.strip():
            raise JobValidationError("script is empty")
        try:
            d = validate_direction(direction)
        except DirectionError as e:
            raise JobValidationError(str(e)) from None
        voice = next((v for v in ve.catalog() if v["voice_id"] == d["voice_id"]), None)
        if voice is None:
            raise JobValidationError(f"unknown voice_id: {d['voice_id']}")
        if not voice["ready"]:
            raise JobValidationError(f"voice {d['voice_id']} is not available: {voice['reason']}")
        if d["language"] not in voice["languages"]:
            raise JobValidationError(
                f"voice {d['voice_id']} does not support language {d['language']} "
                f"(supported: {', '.join(voice['languages']) or 'none'}). No Tamil-capable provider is verified.")
        application = None
        if d.get("style_profile_id"):
            # Resolve (owner-checked; 404 if missing) and actually apply the measured profile, so the
            # profile id can never be a mere echo. Explicit direction fields always win.
            from voice_generation.profile_application import ProfileApplicationError, apply_profile
            profile = self.profile(d["style_profile_id"], owner_id)
            try:
                d, application = apply_profile(profile, direction, d,
                                               supported_controls=voice.get("supported_controls") or ())
            except ProfileApplicationError as e:
                raise JobValidationError(str(e)) from None
        job_id, _ = self._create_job(owner_id, idem, "GENERATION",
                                     {"script": script.strip(), "direction": d, "profile_application": application},
                                     d["language"], f"Voice generation: {script.strip()[:60]}")
        return self.get_job(job_id, owner_id)

    def get_job(self, job_id, owner_id):
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT j.*, v.kind, v.payload_json, v.progress_note, v.result_ref, v.result_json, v.retry_of, "
                          "v.temp_path FROM workflow_jobs j JOIN voice_job_requests v USING(job_id) "
                          "WHERE j.job_id=? AND j.owner_id=?", (job_id, owner_id)).fetchone()
            if r is None:
                raise VoiceNotFound("Voice job not found")
            st = c.execute("SELECT stage_key,status,attempt_count,max_attempts,error,updated_at FROM workflow_stages "
                           "WHERE job_id=?", (job_id,)).fetchone()
            return self._shape(r, st)

    def list_jobs(self, owner_id, kind=None, limit=100):
        with closing(connect(self.database_file)) as c:
            q = ("SELECT j.*, v.kind, v.payload_json, v.progress_note, v.result_ref, v.result_json, v.retry_of, v.temp_path "
                 "FROM workflow_jobs j JOIN voice_job_requests v USING(job_id) WHERE j.owner_id=?")
            a = [owner_id]
            if kind:
                q += " AND v.kind=?"
                a.append(kind)
            rows = c.execute(q + " ORDER BY j.created_at DESC LIMIT ?", (*a, min(limit, 500))).fetchall()
            out = []
            for r in rows:
                st = c.execute("SELECT stage_key,status,attempt_count,max_attempts,error,updated_at FROM workflow_stages "
                               "WHERE job_id=?", (r["job_id"],)).fetchone()
                out.append(self._shape(r, st, brief=True))
            return out

    @staticmethod
    def _shape(r, st, brief=False):
        payload = json.loads(r["payload_json"])
        view = {k: payload[k] for k in ("profile_name", "language", "provenance_state", "base_voice_style_id", "source")
                if k in payload} if r["kind"] == "ANALYSIS" else \
            {"script_chars": len(payload["script"]), "direction": payload["direction"],
             **({} if brief else {"script": payload["script"]})}
        return {"job_id": r["job_id"], "kind": r["kind"], "status": r["status"], "language": r["language"],
                "topic": r["topic"], "created_at": r["created_at"], "updated_at": r["updated_at"],
                "stage": {"key": st["stage_key"], "status": st["status"], "attempt": st["attempt_count"],
                          "max_attempts": st["max_attempts"]} if st else None,
                "progress_note": r["progress_note"], "error": (st["error"] if st and st["error"] else None),
                "retry_of": r["retry_of"], "result_ref": r["result_ref"],
                "result": json.loads(r["result_json"]) if r["result_json"] else None,
                "request": view, "can_cancel": r["status"] in {"QUEUED", "RUNNING"},
                "can_retry": r["status"] in {"FAILED", "CANCELLED"},
                "reference_temp_retained": bool(r["temp_path"])}

    def cancel(self, job_id, owner_id):
        now = _now()
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                j = c.execute("SELECT j.status FROM workflow_jobs j JOIN voice_job_requests v USING(job_id) "
                              "WHERE j.job_id=? AND j.owner_id=?", (job_id, owner_id)).fetchone()
                if j is None:
                    raise VoiceNotFound("Voice job not found")
                if j["status"] == "CANCELLED":
                    c.commit()
                    return self.get_job(job_id, owner_id)
                if j["status"] not in {"QUEUED", "RUNNING"}:
                    raise InvalidTransition(f"Job in {j['status']} cannot be cancelled")
                # Failing the active claim makes WorkerService.complete() raise ClaimLost,
                # so a running worker can never commit a result for a cancelled job.
                c.execute("UPDATE worker_claims SET status='FAILED', finished_at=?, failure_detail='CANCELLED' "
                          "WHERE job_id=? AND status='ACTIVE'", (now, job_id))
                c.execute("UPDATE workflow_stages SET status='FAILED', version=version+1, updated_at=?, error='Cancelled by user' "
                          "WHERE job_id=? AND status IN ('PENDING','RUNNING','FAILED')", (now, job_id))
                c.execute("UPDATE workflow_jobs SET status='CANCELLED', updated_at=? WHERE job_id=?", (now, job_id))
                c.commit()
            except Exception:
                if c.in_transaction:
                    c.rollback()
                raise
        return self.get_job(job_id, owner_id)

    def retry(self, job_id, owner_id, idem):
        """Retry = a NEW job with the same request; the failed job and its history are untouched."""
        old = self.get_job(job_id, owner_id)
        if old["status"] not in {"FAILED", "CANCELLED"}:
            raise InvalidTransition(f"Job in {old['status']} cannot be retried")
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT payload_json, temp_path FROM voice_job_requests WHERE job_id=?", (job_id,)).fetchone()
        payload = json.loads(r["payload_json"])
        if old["kind"] == "ANALYSIS":
            tp = r["temp_path"]
            if not tp or not Path(tp).is_file():
                raise InvalidTransition("Reference upload is no longer retained; upload the file again")
            jid, _ = self._create_job(owner_id, idem, "ANALYSIS", payload, payload["language"], old["topic"],
                                      temp_path=tp, retry_of=job_id)
            with closing(connect(self.database_file)) as c:   # ownership of the temp file moves to the new job
                c.execute("UPDATE voice_job_requests SET temp_path=NULL WHERE job_id=?", (job_id,))
            return self.get_job(jid, owner_id)
        jid, _ = self._create_job(owner_id, idem, "GENERATION", payload, payload["direction"]["language"],
                                  old["topic"], retry_of=job_id)
        return self.get_job(jid, owner_id)

    def discard_reference(self, job_id, owner_id):
        job = self.get_job(job_id, owner_id)
        if job["status"] in {"QUEUED", "RUNNING"}:
            raise InvalidTransition("Job is active; cancel it first")
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT temp_path FROM voice_job_requests WHERE job_id=?", (job_id,)).fetchone()
            if r and r["temp_path"]:
                try:
                    Path(r["temp_path"]).unlink()
                except OSError:
                    pass
                c.execute("UPDATE voice_job_requests SET temp_path=NULL, updated_at=? WHERE job_id=?", (_now(), job_id))
        return self.get_job(job_id, owner_id)

    def purge_stale_uploads(self, max_age_seconds: float = 7 * 86400) -> int:
        """Retention policy: unreferenced/aged temporary reference uploads are removed."""
        n = 0
        with closing(connect(self.database_file)) as c:
            live = {r[0] for r in c.execute("SELECT temp_path FROM voice_job_requests WHERE temp_path IS NOT NULL")}
        for f in self.upload_dir.glob("*"):
            if str(f) not in live and time.time() - f.stat().st_mtime > 60:
                f.unlink(missing_ok=True); n += 1
            elif time.time() - f.stat().st_mtime > max_age_seconds:
                f.unlink(missing_ok=True); n += 1
        return n

    # --------------------------------------------------------------- profiles
    def _owned_profile_row(self, c, vid, owner_id):
        r = c.execute("SELECT p.voice_style_id, s.metadata_json, s.provenance_state, s.authorization_ref, s.location "
                      "FROM voice_profiles p JOIN voice_sources s USING(source_id) WHERE p.voice_style_id=?", (vid,)).fetchone()
        if r is None or json.loads(r["metadata_json"]).get("owner_id") != owner_id:
            raise VoiceNotFound("Voice style profile not found")
        return r

    def profile(self, vid, owner_id, version=None):
        ve.ensure_path()
        with closing(connect(self.database_file)) as c:
            src = self._owned_profile_row(c, vid, owner_id)
            q, a = "SELECT * FROM voice_profile_versions WHERE voice_style_id=?", [vid]
            if version:
                q += " AND version=?"; a.append(version)
            r = c.execute(q + " ORDER BY version DESC LIMIT 1", a).fetchone()
            if r is None:
                raise VoiceNotFound("Profile version not found")
            p = c.execute("SELECT * FROM voice_profiles WHERE voice_style_id=?", (vid,)).fetchone()
            meta = json.loads(src["metadata_json"])
            versions = [dict(x) for x in c.execute(
                "SELECT version, analysis_hash, analysis_version, note, created_at FROM voice_profile_versions "
                "WHERE voice_style_id=? ORDER BY version", (vid,))]
            body = json.loads(r["profile_json"])
            return {"voice_style_id": vid, "profile_name": p["profile_name"], "source_id": p["source_id"],
                    "version": r["version"], "analysis_hash": r["analysis_hash"], "created_at": r["created_at"],
                    "versions": versions, "read_only": True,
                    "provenance": {"state": src["provenance_state"], "authorization_ref": src["authorization_ref"],
                                   "original_name": meta.get("original_name"), "source_sha256": meta.get("sha256"),
                                   "raw_audio_retained": bool(meta.get("raw_audio_retained"))},
                    "limitations": ["Emotion labels are heuristic inferences, not validated classification.",
                                    "Syllable rate is estimated from the energy envelope, not measured from phonemes.",
                                    "Word-level emphasis text is unavailable without transcript alignment."],
                    **body}

    def profile_segments(self, vid, owner_id, version=None):
        p = self.profile(vid, owner_id, version)
        with closing(connect(self.database_file)) as c:
            rows = c.execute("SELECT segment_json FROM voice_style_segments WHERE voice_style_id=? AND version=? "
                             "ORDER BY start_ms", (vid, p["version"])).fetchall()
        return {"voice_style_id": vid, "version": p["version"], "segments": [json.loads(r[0]) for r in rows]}

    def list_profiles(self, owner_id):
        with closing(connect(self.database_file)) as c:
            rows = c.execute(
                "SELECT p.voice_style_id, p.profile_name, p.language, p.created_at, s.provenance_state, s.metadata_json, "
                "(SELECT MAX(version) FROM voice_profile_versions v WHERE v.voice_style_id=p.voice_style_id) AS latest "
                "FROM voice_profiles p JOIN voice_sources s USING(source_id) ORDER BY p.created_at DESC").fetchall()
        return [{"voice_style_id": r["voice_style_id"], "profile_name": r["profile_name"], "language": r["language"],
                 "created_at": r["created_at"], "latest_version": r["latest"], "provenance_state": r["provenance_state"]}
                for r in rows if json.loads(r["metadata_json"]).get("owner_id") == owner_id]

    def compare(self, vid, owner_id, a, b):
        pa, pb = self.profile(vid, owner_id, a), self.profile(vid, owner_id, b)
        if pa["version"] == pb["version"]:
            raise JobValidationError("Choose two different versions")

        def flat(d, pre=""):
            out = {}
            for k, v in d.items():
                if k in ("contour_hz", "pauses", "segments", "phrase_duration_ms", "candidates", "transitions"):
                    continue
                if isinstance(v, dict):
                    out.update(flat(v, f"{pre}{k}."))
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[pre + k] = v
            return out
        sections = ("pitch", "speaking_rate", "tempo", "pause_profile", "energy_profile", "rhythm_profile", "confidence",
                    "audio")
        fa = flat({k: pa[k] for k in sections if k in pa}); fb = flat({k: pb[k] for k in sections if k in pb})
        rows = [{"metric": k, "a": fa.get(k), "b": fb.get(k),
                 "delta": round(fb[k] - fa[k], 4) if k in fa and k in fb else None} for k in sorted(set(fa) | set(fb))]
        return {"voice_style_id": vid, "a": pa["version"], "b": pb["version"],
                "hash_a": pa["analysis_hash"], "hash_b": pb["analysis_hash"],
                "identical": pa["analysis_hash"] == pb["analysis_hash"], "metrics": rows}

    # ------------------------------------------------------------ generations
    def _owned_generation(self, gid, owner_id):
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT 1 FROM voice_job_requests WHERE kind='GENERATION' AND result_ref=? AND owner_id=?",
                          (gid, owner_id)).fetchone()
            if r is None:
                raise VoiceNotFound("Generation not found")
            return c.execute("SELECT * FROM voice_generations WHERE generation_id=?", (gid,)).fetchone()

    def _profile_application(self, gid):
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT payload_json FROM voice_job_requests WHERE kind='GENERATION' AND result_ref=?",
                          (gid,)).fetchone()
        return (json.loads(r["payload_json"]).get("profile_application") if r else None)

    def generation(self, gid, owner_id):
        g = self._owned_generation(gid, owner_id)
        return {"generation_id": gid, "voice_id": g["voice_id"], "style_profile_id": g["style_profile_id"],
                "language": g["language"], "audio_format": g["audio_format"], "sample_rate": g["sample_rate"],
                "duration_ms": g["duration_ms"], "generation_version": g["generation_version"],
                "generation_hash": g["generation_hash"], "status": g["status"], "error": g["error"],
                "effective_direction": json.loads(g["direction_json"]),
                "profile_application": self._profile_application(gid),
                "audio_url": f"/api/voice/generations/{gid}/audio" if g["status"] == "COMPLETED" else None,
                "voice": next((v for v in ve.catalog() if v["voice_id"] == g["voice_id"]), None)}

    def generation_audio_path(self, gid, owner_id) -> Path:
        g = self._owned_generation(gid, owner_id)
        if g["status"] != "COMPLETED" or not g["audio_reference"]:
            raise VoiceNotFound("Audio is not available")
        p = Path(g["audio_reference"]).resolve()
        root = self.audio_dir.resolve()
        if root not in p.parents or not p.is_file():
            raise VoiceNotFound("Audio is not available")
        return p

    def alignment(self, gid, owner_id):
        self._owned_generation(gid, owner_id)
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT alignment_json FROM voice_alignments WHERE generation_id=?", (gid,)).fetchone()
        if r is None:
            raise VoiceNotFound("Alignment not available")
        a = json.loads(r[0])
        a["forced_alignment"] = ve.aligner_status()
        return a

    def providers(self):
        return {"voices": ve.catalog(), "aligner": ve.aligner_status(),
                "limitations": ["Tamil speech synthesis is not verified for any listed voice.",
                                "Word timings are proportional estimates unless a forced aligner has run.",
                                "Emotion labels are heuristic inferences."],
                "direction_ranges": {"tempo": [0.5, 2.0], "pitch_variation": [0, 1], "energy": [0, 1],
                                     "emotion.intensity": [0, 1], "emphasis.strength": [0, 1],
                                     "pause.duration_ms": [0, 5000]}}

    # ---------------------------------------------------------------- handlers
    def _row(self, job_id):
        with closing(connect(self.database_file)) as c:
            return c.execute("SELECT * FROM voice_job_requests WHERE job_id=?", (job_id,)).fetchone()

    def _set(self, job_id, **cols):
        with closing(connect(self.database_file)) as c:
            sets = ", ".join(f"{k}=?" for k in cols)
            c.execute(f"UPDATE voice_job_requests SET {sets}, updated_at=? WHERE job_id=?", (*cols.values(), _now(), job_id))

    def _cancelled(self, job_id, cancel):
        if cancel.is_set():
            return True
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (job_id,)).fetchone()
        return r is None or r["status"] == "CANCELLED"

    def analysis_handler(self, context, cancel):
        row = self._row(context.job_id)
        payload = json.loads(row["payload_json"])
        path = row["temp_path"]
        if not path or not Path(path).is_file():
            raise RuntimeError("Reference upload is missing; upload the file again")
        self._set(context.job_id, progress_note="Decoding and analyzing audio")
        svc = ve.build_analyzer(self.database_file)
        src = payload["source"]
        sid = svc.register_source(
            kind="LOCAL_FILE", location=f"upload:{src['sha256'][:16]}", authorization_ref=payload["authorization_ref"],
            provenance_state=payload["provenance_state"], retain_audio=self.retain_reference,
            metadata={"owner_id": context.owner_id, "job_id": context.job_id, "original_name": src["original_name"],
                      "sha256": src["sha256"], "size_bytes": src["size_bytes"],
                      "raw_audio_retained": self.retain_reference})
        prof = svc.analyze(sid, profile_name=payload["profile_name"], language=payload["language"],
                           transcript=payload.get("transcript"), audio_path=path,
                           base_voice_style_id=payload.get("base_voice_style_id"),
                           note=f"job {context.job_id}", should_abort=lambda: self._cancelled(context.job_id, cancel))
        if self._cancelled(context.job_id, cancel):
            raise RuntimeError("cancelled")
        self._set(context.job_id, progress_note="Completed", result_ref=prof["voice_style_id"],
                  result_json=_canon({"voice_style_id": prof["voice_style_id"], "version": prof["version"],
                                      "analysis_hash": prof["analysis_hash"],
                                      "confidence": prof["confidence"]["overall"],
                                      "segments": prof["audio"]["segment_count"]}))
        if not self.retain_reference:
            try:
                Path(path).unlink()
            except OSError:
                pass
            self._set(context.job_id, temp_path=None)
        return []

    def generation_handler(self, context, cancel):
        row = self._row(context.job_id)
        payload = json.loads(row["payload_json"])
        engine = ve.build_engine(self.database_file, self.audio_dir)
        res = engine.generate(
            payload["script"], payload["direction"],
            should_abort=lambda: self._cancelled(context.job_id, cancel),
            progress=lambda note: self._set(context.job_id, progress_note=note))
        if self._cancelled(context.job_id, cancel):
            raise RuntimeError("cancelled")
        self._set(context.job_id, progress_note="Completed", result_ref=res["generation_id"],
                  result_json=_canon({"generation_id": res["generation_id"], "duration_ms": res["duration_ms"],
                                      "generation_hash": res["generation_hash"], "voice_id": res["voice_id"],
                                      "sample_rate": res["sample_rate"],
                                      "profile_application": payload.get("profile_application")}))
        return []

    def handlers(self):
        return {STAGE_ANALYSIS: self.analysis_handler, STAGE_GENERATION: self.generation_handler}
