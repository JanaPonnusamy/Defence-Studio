"""Narrator datasets: ingest, QC, segmentation, transcripts, duplicates, splits, versions (VL-3).

* A dataset belongs to exactly one narrator and one consent record (TRAINING scope).
* A recording already present in ANOTHER narrator's dataset is rejected (identity mixing guard).
* Originals are stored untouched (SOURCE_RECORDING); segments are derived 24 kHz mono WAVs.
* Transcripts are human-entered/reviewed per segment. No Tamil forced aligner is installed,
  so word alignment is reported as NOT_ALIGNED rather than estimated.
* A dataset version is an immutable manifest (hash-addressed) and is what training consumes.
"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from contextlib import closing
from pathlib import Path

import numpy as np

from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab.common import VoiceLabError, canon, loads, new_id, now
from defence_studio.voice_lab.context import Ctx
from defence_studio.voice_lab.narrators import Narrators

SEG_SR = 24000
TARGET_HOURS = 10.0


def min_ready_minutes() -> float:
    return float(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_MIN_DATASET_MINUTES", 60))


def max_file_minutes() -> float:
    return float(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_MAX_FILE_MINUTES", 120))


def tamil_ratio(text: str) -> float:
    letters = [ch for ch in text if unicodedata.category(ch)[0] in "LM"]
    if not letters:
        return 0.0
    return sum(0x0B80 <= ord(ch) <= 0x0BFF for ch in letters) / len(letters)


def plan_segments(x: np.ndarray, sr: int, *, min_ms=1500, target_ms=8000, max_ms=15000, pad_ms=120):
    """Group speech regions into training-sized segments; split over-long regions at the quietest frame."""
    regions = A.speech_regions(x, sr, min_silence_ms=350, min_speech_ms=200)
    db = A.frame_db(x, sr)
    pieces = []
    for s, e in regions:
        stack = [(s, e)]
        while stack:
            a, b = stack.pop()
            if b - a <= max_ms:
                pieces.append((a, b))
                continue
            lo, hi = (a + min_ms) // 10, (b - min_ms) // 10
            cut = (lo + int(np.argmin(db[lo:hi]))) * 10 if hi > lo else (a + b) // 2
            stack += [(cut, b), (a, cut)]
    pieces.sort()
    segs, cur = [], None
    for a, b in pieces:
        if cur and (b - cur[0]) <= target_ms and a - cur[1] < 900:
            cur = (cur[0], b)
        else:
            if cur:
                segs.append(cur)
            cur = (a, b)
    if cur:
        segs.append(cur)
    total = int(len(x) * 1000 / sr)
    return [(max(0, a - pad_ms), min(total, b + pad_ms)) for a, b in segs]


class Datasets:
    def __init__(self, ctx: Ctx, narrators: Narrators):
        self.ctx, self.narr = ctx, narrators

    def create(self, owner_id, *, narrator_id, name, language, consent_id):
        n = self.narr.get(narrator_id, owner_id)
        if n["status"] != "ACTIVE":
            raise VoiceLabError(409, "narrator is archived")
        if language not in n["languages"]:
            raise VoiceLabError(422, f"narrator does not list language {language}")
        self.narr.require_consent(consent_id, owner_id, "TRAINING")
        con = self.narr.consent(consent_id, owner_id)
        if con["subject_role"] != "NARRATOR":
            raise VoiceLabError(409, {"code": "CONSENT_ROLE",
                                      "detail": "training datasets need a NARRATOR consent; reference-speaker consent "
                                                "never authorises identity training"})
        did, ts = new_id("ds"), now()
        try:
            with self.ctx.tx() as c:
                c.execute("INSERT INTO vl_voice_datasets VALUES(?,?,?,?,?,?,'DRAFT',?,?)",
                          (did, owner_id, narrator_id, name.strip(), language, consent_id, ts, ts))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise VoiceLabError(409, "dataset name already used for this narrator") from None
            raise
        return self.get(did, owner_id)

    def _row(self, did, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT * FROM vl_voice_datasets WHERE dataset_id=? AND owner_id=?", (did, owner_id)).fetchone()
        if not r:
            raise VoiceLabError(404, "dataset not found")
        return dict(r)

    def get(self, did, owner_id):
        d = self._row(did, owner_id)
        with closing(self.ctx.conn()) as c:
            d["files"] = [dict(r) | {"qc": loads(r["qc_json"], {})} for r in
                          c.execute("SELECT file_id, original_name, source_note, sha256, duration_ms, status, qc_json, "
                                    "created_at FROM vl_dataset_files WHERE dataset_id=? ORDER BY created_at", (did,))]
            d["versions"] = [dict(r) | {"stats": loads(r["stats_json"], {})} for r in
                             c.execute("SELECT dataset_version_id, version, manifest_sha256, status, approved_by, "
                                       "approved_at, stats_json, created_at FROM vl_dataset_versions WHERE dataset_id=? "
                                       "ORDER BY version", (did,))]
        for f in d["files"]:
            f.pop("qc_json", None)
        for v in d["versions"]:
            v.pop("stats_json", None)
        d["readiness"] = self.readiness(did, owner_id)
        return d

    def list(self, owner_id, narrator_id=None):
        q, args = "SELECT dataset_id FROM vl_voice_datasets WHERE owner_id=?", [owner_id]
        if narrator_id:
            q += " AND narrator_id=?"
            args.append(narrator_id)
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute(q + " ORDER BY created_at", args)]
        return [self._row(i, owner_id) | {"readiness": self.readiness(i, owner_id)} for i in ids]

    # ------------------------------------------------------------- ingest
    def add_file(self, owner_id, did, *, temp_path: Path, original_name: str, source_note: str):
        ds = self._row(did, owner_id)
        if ds["status"] == "ARCHIVED":
            raise VoiceLabError(409, "dataset is archived")
        if not (source_note or "").strip():
            raise VoiceLabError(422, "source_note is required (who recorded it, when, under which consent)")
        ext = Path(original_name or "").suffix.lower()
        if ext not in A.SUPPORTED_EXT:
            raise VoiceLabError(422, f"unsupported audio format {ext or '(none)'}")
        from defence_studio.voice_lab.common import sha256_file
        sha = sha256_file(temp_path)
        with closing(self.ctx.conn()) as c:
            other = c.execute("SELECT d.narrator_id, d.dataset_id FROM vl_dataset_files f JOIN vl_voice_datasets d "
                              "USING(dataset_id) WHERE f.sha256=? AND d.owner_id=?", (sha, owner_id)).fetchall()
        for o in other:
            if o["narrator_id"] != ds["narrator_id"]:
                raise VoiceLabError(409, {"code": "IDENTITY_MIXING",
                                          "detail": "this recording is already in another narrator's dataset"})
            if o["dataset_id"] == did:
                raise VoiceLabError(409, {"code": "DUPLICATE_FILE", "detail": "identical file already in this dataset"})
        x = A.decode(temp_path, SEG_SR, max_seconds=max_file_minutes() * 60 + 1)
        dur_ms = int(len(x) * 1000 / SEG_SR)
        if dur_ms > max_file_minutes() * 60000:
            raise VoiceLabError(413, f"file longer than {max_file_minutes():.0f} min; split it before upload")
        q = A.quality(x, SEG_SR)
        art = self.ctx.register_file(owner_id, temp_path, kind="SOURCE_RECORDING", fmt=ext.lstrip("."), move=True,
                                     duration_ms=dur_ms, source={"dataset_id": did, "original_name": original_name,
                                                                 "narrator_id": ds["narrator_id"]})
        fid = new_id("dsf")
        with self.ctx.tx() as c:
            c.execute("INSERT INTO vl_dataset_files VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (fid, did, art["artifact_id"], os.path.basename(original_name)[:200], source_note.strip(), sha,
                       dur_ms, canon(q), "QUEUED", now()))
        return {"file_id": fid, "artifact_id": art["artifact_id"], "duration_ms": dur_ms, "qc": q, "status": "QUEUED"}

    def segment_file(self, owner_id, fid, should_abort=lambda: False):
        """Segment one stored original into QC'd segments. Idempotent: re-running replaces nothing done."""
        with closing(self.ctx.conn()) as c:
            f = c.execute("SELECT f.*, d.owner_id, d.narrator_id FROM vl_dataset_files f JOIN vl_voice_datasets d "
                          "USING(dataset_id) WHERE file_id=?", (fid,)).fetchone()
            done = c.execute("SELECT COUNT(*) FROM vl_voice_dataset_segments WHERE file_id=?", (fid,)).fetchone()[0]
        if not f or f["owner_id"] != owner_id:
            raise VoiceLabError(404, "dataset file not found")
        if f["status"] == "SEGMENTED":
            return done
        src = self.ctx.artifact_path(f["artifact_id"], owner_id, verify=True)
        x = A.decode(src, SEG_SR, max_seconds=max_file_minutes() * 60 + 1)
        plan = plan_segments(x, SEG_SR)
        floor = A.quality(x, SEG_SR)["noise_floor_dbfs"]  # recording-level floor (pauses included)
        with self.ctx.tx() as c:  # a crash mid-way leaves partial rows: clear them and redo deterministically
            c.execute("DELETE FROM vl_voice_dataset_segments WHERE file_id=?", (fid,))
        rows = []
        for i, (a, b) in enumerate(plan):
            if should_abort():
                raise RuntimeError("cancelled")
            seg = x[int(a * SEG_SR / 1000):int(b * SEG_SR / 1000)]
            q = A.quality(seg, SEG_SR, noise_floor_db=floor)
            status, issues = A.qc_verdict(q)
            q["issues"] = issues
            art = self.ctx.register_audio(owner_id, seg, SEG_SR, kind="DATASET_SEGMENT",
                                          source={"file_id": fid, "start_ms": a, "end_ms": b,
                                                  "narrator_id": f["narrator_id"]})
            rows.append((new_id("seg"), f["dataset_id"], fid, art["artifact_id"], i, a, b, b - a, None, "NONE",
                         canon(q), status, A.fingerprint(seg, SEG_SR), None, 0, None, now(), now()))
        with self.ctx.tx() as c:
            c.executemany("INSERT INTO vl_voice_dataset_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            c.execute("UPDATE vl_dataset_files SET status='SEGMENTED' WHERE file_id=?", (fid,))
        self.detect_duplicates(f["dataset_id"], owner_id)
        return len(rows)

    def detect_duplicates(self, did, owner_id, threshold=0.93):
        self._row(did, owner_id)
        with closing(self.ctx.conn()) as c:
            segs = [dict(r) for r in c.execute("SELECT segment_id, fingerprint, duration_ms FROM vl_voice_dataset_segments "
                                               "WHERE dataset_id=? ORDER BY created_at, ordinal", (did,))]
        dup = {}
        for i, s in enumerate(segs):
            for t in segs[:i]:
                if t["segment_id"] in dup:
                    continue
                if abs(s["duration_ms"] - t["duration_ms"]) <= 0.05 * max(s["duration_ms"], t["duration_ms"]) and \
                        A.fingerprint_similarity(s["fingerprint"], t["fingerprint"]) >= threshold:
                    dup[s["segment_id"]] = t["segment_id"]
                    break
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_voice_dataset_segments SET duplicate_of=NULL WHERE dataset_id=?", (did,))
            c.executemany("UPDATE vl_voice_dataset_segments SET duplicate_of=? WHERE segment_id=?",
                          [(v, k) for k, v in dup.items()])
        return len(dup)

    # -------------------------------------------------------- segments
    def segments(self, did, owner_id, limit=500, offset=0):
        self._row(did, owner_id)
        with closing(self.ctx.conn()) as c:
            rows = c.execute("SELECT * FROM vl_voice_dataset_segments WHERE dataset_id=? ORDER BY file_id, ordinal "
                             "LIMIT ? OFFSET ?", (did, limit, offset)).fetchall()
            total = c.execute("SELECT COUNT(*) FROM vl_voice_dataset_segments WHERE dataset_id=?", (did,)).fetchone()[0]
        items = []
        for r in rows:
            d = dict(r)
            d["qc"] = loads(d.pop("qc_json"), {})
            d["excluded"] = bool(d["excluded"])
            d["alignment_status"] = "NOT_ALIGNED"
            d.pop("fingerprint")
            items.append(d)
        return {"total": total, "items": items}

    def update_segment(self, owner_id, sid, *, transcript=None, transcript_status=None, excluded=None):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT s.*, d.owner_id, d.language FROM vl_voice_dataset_segments s JOIN vl_voice_datasets d "
                          "USING(dataset_id) WHERE segment_id=?", (sid,)).fetchone()
        if not r or r["owner_id"] != owner_id:
            raise VoiceLabError(404, "segment not found")
        fields, warn = {}, []
        if transcript is not None:
            t = unicodedata.normalize("NFC", transcript.strip())
            if len(t) > 2000:
                raise VoiceLabError(422, "transcript too long")
            fields["transcript"] = t or None
            if r["language"] == "ta" and t and tamil_ratio(t) < 0.6:
                warn.append("TRANSCRIPT_NOT_MOSTLY_TAMIL_SCRIPT")
            fields["transcript_status"] = transcript_status or ("DRAFT" if t else "NONE")
        elif transcript_status is not None:
            fields["transcript_status"] = transcript_status
        if fields.get("transcript_status") not in (None, "NONE", "DRAFT", "REVIEWED"):
            raise VoiceLabError(422, "transcript_status must be NONE, DRAFT or REVIEWED")
        # The transcript this call leaves behind: a supplied (possibly cleared) one wins over the stored one.
        resulting = fields["transcript"] if "transcript" in fields else r["transcript"]
        if fields.get("transcript_status") == "REVIEWED" and not resulting:
            raise VoiceLabError(422, "cannot mark an empty transcript REVIEWED")
        if excluded is not None:
            fields["excluded"] = int(bool(excluded))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self.ctx.tx() as c:
                c.execute(f"UPDATE vl_voice_dataset_segments SET {sets}, updated_at=? WHERE segment_id=?",
                          (*fields.values(), now(), sid))
        return {"segment_id": sid, "updated": sorted(fields), "warnings": warn}

    # ------------------------------------------------------ readiness/split
    def _eligible(self, r) -> bool:
        return (not r["excluded"] and r["qc_status"] != "FAIL" and r["duplicate_of"] is None
                and r["transcript_status"] == "REVIEWED")

    def readiness(self, did, owner_id):
        ds = self._row(did, owner_id)
        with closing(self.ctx.conn()) as c:
            segs = c.execute("SELECT * FROM vl_voice_dataset_segments WHERE dataset_id=?", (did,)).fetchall()
            files = c.execute("SELECT status, duration_ms FROM vl_dataset_files WHERE dataset_id=?", (did,)).fetchall()
        missing = []
        try:
            self.narr.require_consent(ds["consent_id"], owner_id, "TRAINING")
            consent_ok = True
        except VoiceLabError as e:
            consent_ok = False
            missing.append(e.detail if isinstance(e.detail, dict) else {"code": "CONSENT", "detail": e.detail})
        elig = [r for r in segs if self._eligible(r)]
        elig_ms = sum(r["duration_ms"] for r in elig)
        n = len(segs)
        stats = {
            "files": len(files), "files_pending": sum(f["status"] != "SEGMENTED" for f in files),
            "source_duration_ms": sum(f["duration_ms"] for f in files),
            "segments": n, "segment_duration_ms": sum(r["duration_ms"] for r in segs),
            "qc": {k: sum(r["qc_status"] == k for r in segs) for k in ("PASS", "WARN", "FAIL")},
            "duplicates": sum(r["duplicate_of"] is not None for r in segs),
            "excluded": sum(bool(r["excluded"]) for r in segs),
            "transcripts": {k: sum(r["transcript_status"] == k for r in segs) for k in ("NONE", "DRAFT", "REVIEWED")},
            "transcript_coverage": round(sum(r["transcript_status"] == "REVIEWED" for r in segs) / n, 4) if n else 0.0,
            "eligible_segments": len(elig), "eligible_duration_ms": elig_ms,
            "target_hours": TARGET_HOURS, "eligible_hours": round(elig_ms / 3.6e6, 3),
            "minimum_minutes_for_approval": min_ready_minutes(),
            "alignment": "NOT_ALIGNED (no licensed Tamil forced aligner installed)",
        }
        if not files:
            missing.append({"code": "NO_RECORDINGS", "detail": "no authorised recordings uploaded"})
        if stats["files_pending"]:
            missing.append({"code": "SEGMENTATION_PENDING", "detail": f"{stats['files_pending']} file(s) not segmented yet"})
        if n and stats["transcripts"]["REVIEWED"] < n - stats["excluded"]:
            missing.append({"code": "TRANSCRIPTS_INCOMPLETE",
                            "detail": f"{n - stats['excluded'] - stats['transcripts']['REVIEWED']} included segment(s) "
                                      "lack a reviewed transcript"})
        if elig_ms < min_ready_minutes() * 60000:
            missing.append({"code": "INSUFFICIENT_DURATION",
                            "detail": f"{elig_ms / 60000:.1f} eligible min < {min_ready_minutes():.0f} min minimum "
                                      f"(target {TARGET_HOURS:.0f} h; duration alone does not prove quality)"})
        status = "BLOCKED" if not consent_ok or not files else ("READY" if not missing else "NOT_READY")
        return {"status": status, "missing": missing, "stats": stats}

    @staticmethod
    def split_of(segment_id: str, artifact_sha: str) -> str:
        b = int(hashlib.sha256(f"{segment_id}:{artifact_sha}".encode()).hexdigest()[:8], 16) % 100
        return "train" if b < 90 else ("validation" if b < 95 else "test")

    def create_version(self, owner_id, did):
        ds = self._row(did, owner_id)
        with closing(self.ctx.conn()) as c:
            segs = c.execute("SELECT s.*, a.sha256 AS art_sha, a.rel_path FROM vl_voice_dataset_segments s "
                             "JOIN vl_audio_artifacts a USING(artifact_id) WHERE dataset_id=? ORDER BY file_id, ordinal",
                             (did,)).fetchall()
            ver = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM vl_dataset_versions WHERE dataset_id=?",
                            (did,)).fetchone()[0]
        items = []
        for r in segs:
            if not self._eligible(r):
                continue
            split = self.split_of(r["segment_id"], r["art_sha"])
            items.append({"segment_id": r["segment_id"], "artifact_sha256": r["art_sha"], "rel_path": r["rel_path"],
                          "duration_ms": r["duration_ms"], "transcript": r["transcript"], "split": split})
        if not items:
            raise VoiceLabError(409, {"code": "NO_ELIGIBLE_SEGMENTS", "readiness": self.readiness(did, owner_id)})
        manifest = {"schema": "defence-studio.voice-lab-dataset.v1", "dataset_id": did, "version": ver,
                    "narrator_id": ds["narrator_id"], "language": ds["language"], "consent_id": ds["consent_id"],
                    "sample_rate": SEG_SR, "segments": items}
        data = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
        art = self.ctx.register_bytes(owner_id, data, kind="DATASET_MANIFEST", fmt="json", ext=".json",
                                      source={"dataset_id": did, "version": ver})
        stats = {"segments": len(items), "duration_ms": sum(i["duration_ms"] for i in items),
                 "splits": {s: sum(i["split"] == s for i in items) for s in ("train", "validation", "test")},
                 "readiness_at_creation": self.readiness(did, owner_id)["status"]}
        vid = new_id("dsv")
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_voice_dataset_segments SET split=NULL WHERE dataset_id=?", (did,))
            c.executemany("UPDATE vl_voice_dataset_segments SET split=? WHERE segment_id=?",
                          [(i["split"], i["segment_id"]) for i in items])
            c.execute("INSERT INTO vl_dataset_versions VALUES(?,?,?,?,?,?,?,'PENDING_APPROVAL',NULL,NULL,?)",
                      (vid, did, ds["narrator_id"], ver, art["rel_path"], art["sha256"], canon(stats), now()))
        return self.version(vid, owner_id)

    def version(self, vid, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT v.*, d.owner_id FROM vl_dataset_versions v JOIN vl_voice_datasets d USING(dataset_id) "
                          "WHERE dataset_version_id=?", (vid,)).fetchone()
        if not r or r["owner_id"] != owner_id:
            raise VoiceLabError(404, "dataset version not found")
        d = dict(r)
        d["stats"] = loads(d.pop("stats_json"), {})
        d.pop("owner_id")
        return d

    def approve_version(self, owner_id, vid, approver):
        v = self.version(vid, owner_id)
        ready = self.readiness(v["dataset_id"], owner_id)
        if ready["status"] != "READY":
            raise VoiceLabError(409, {"code": "DATASET_NOT_READY", "readiness": ready})
        p = self.ctx.storage_root / v["manifest_rel_path"]
        from defence_studio.voice_lab.common import sha256_file
        if not p.is_file() or sha256_file(p) != v["manifest_sha256"]:
            raise VoiceLabError(409, {"code": "MANIFEST_INTEGRITY", "detail": "manifest missing or modified"})
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_dataset_versions SET status='APPROVED', approved_by=?, approved_at=? "
                      "WHERE dataset_version_id=?", (approver, now(), vid))
            c.execute("UPDATE vl_voice_datasets SET status='APPROVED', updated_at=? WHERE dataset_id=?",
                      (now(), v["dataset_id"]))
        return self.version(vid, owner_id)
