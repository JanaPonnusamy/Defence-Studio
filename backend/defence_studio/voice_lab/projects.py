"""Non-destructive multi-track audio projects: edit operations, undo/redo, persistence, render/export.

Source artifacts are never modified. A project is a list of tracks holding clips that
reference artifacts by id with in/out windows, gain and fades. Every mutation is an
operation logged with the full before/after timeline document, so undo/redo is exact
and survives restarts. The normalised ``vl_audio_tracks``/``vl_audio_clips`` rows are
rewritten in the same transaction as the log entry.
"""
from __future__ import annotations

import copy
from contextlib import closing

import numpy as np

from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab.common import VoiceLabError, canon, loads, new_id, now, sha256_bytes
from defence_studio.voice_lab.context import Ctx

DOC_SCHEMA = "defence-studio.voice-lab-project.v1"
ROLES = {"male_reference", "male_narrator", "female_narrator", "female_styled", "alternative", "music", "other"}
TRACK_FIELDS = {"name", "role", "gain_db", "mute", "solo", "locked", "effects"}
CLIP_FIELDS = {"start_ms", "in_ms", "out_ms", "gain_db", "fade_in_ms", "fade_out_ms", "crossfade_next_ms", "track_id"}
EFFECTS = {"eq", "highpass", "lowpass", "denoise"}


def _clip_len(c):
    return c["out_ms"] - c["in_ms"]


class Projects:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    # ------------------------------------------------------------- load/save
    def create(self, owner_id, *, name, sample_rate=48000):
        if sample_rate not in (16000, 22050, 24000, 44100, 48000):
            raise VoiceLabError(422, "sample_rate must be 16000/22050/24000/44100/48000")
        pid, ts = new_id("proj"), now()
        with self.ctx.tx() as c:
            c.execute("INSERT INTO vl_audio_projects VALUES(?,?,?,0,?,'ACTIVE',?,?)",
                      (pid, owner_id, (name or "Untitled").strip()[:200], sample_rate, ts, ts))
        return self.doc(pid, owner_id)

    def _project(self, c, pid, owner_id):
        r = c.execute("SELECT * FROM vl_audio_projects WHERE project_id=? AND owner_id=?", (pid, owner_id)).fetchone()
        if not r:
            raise VoiceLabError(404, "project not found")
        return r

    def doc(self, pid, owner_id, c=None):
        own = c is None
        c = c or self.ctx.conn()
        try:
            p = self._project(c, pid, owner_id)
            tracks = []
            for t in c.execute("SELECT * FROM vl_audio_tracks WHERE project_id=? ORDER BY ordinal", (pid,)):
                clips = [{k: r[k] for k in ("clip_id", "artifact_id", "start_ms", "in_ms", "out_ms", "gain_db",
                                            "fade_in_ms", "fade_out_ms", "crossfade_next_ms")}
                         for r in c.execute("SELECT * FROM vl_audio_clips WHERE track_id=? ORDER BY start_ms, clip_id",
                                            (t["track_id"],))]
                tracks.append({"track_id": t["track_id"], "name": t["name"], "role": t["role"], "gain_db": t["gain_db"],
                               "mute": bool(t["mute"]), "solo": bool(t["solo"]), "locked": bool(t["locked"]),
                               "effects": loads(t["effects_json"], []), "clips": clips})
            undo = c.execute("SELECT COUNT(*) FROM vl_audio_edit_operations WHERE project_id=? AND undone=0",
                             (pid,)).fetchone()[0]
            redo = c.execute("SELECT COUNT(*) FROM vl_audio_edit_operations WHERE project_id=? AND undone=1",
                             (pid,)).fetchone()[0]
            return {"schema": DOC_SCHEMA, "project_id": pid, "name": p["name"], "revision": p["revision"],
                    "sample_rate": p["sample_rate"], "status": p["status"], "updated_at": p["updated_at"],
                    "tracks": tracks, "can_undo": undo > 0, "can_redo": redo > 0,
                    "duration_ms": max([cl["start_ms"] + _clip_len(cl) for t in tracks for cl in t["clips"]] or [0])}
        finally:
            if own:
                c.close()

    def list(self, owner_id):
        with closing(self.ctx.conn()) as c:
            rows = c.execute("SELECT project_id, name, revision, updated_at, status FROM vl_audio_projects WHERE owner_id=? "
                             "ORDER BY updated_at DESC", (owner_id,)).fetchall()
        return [dict(r) for r in rows]

    def _write(self, c, pid, doc):
        c.execute("DELETE FROM vl_audio_clips WHERE project_id=?", (pid,))
        c.execute("DELETE FROM vl_audio_tracks WHERE project_id=?", (pid,))
        for i, t in enumerate(doc["tracks"]):
            c.execute("INSERT INTO vl_audio_tracks VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (t["track_id"], pid, i, t["name"], t["role"], float(t["gain_db"]), int(t["mute"]), int(t["solo"]),
                       int(t["locked"]), canon(t.get("effects") or [])))
            for cl in t["clips"]:
                c.execute("INSERT INTO vl_audio_clips VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (cl["clip_id"], t["track_id"], pid, cl["artifact_id"], int(cl["start_ms"]), int(cl["in_ms"]),
                           int(cl["out_ms"]), float(cl["gain_db"]), int(cl["fade_in_ms"]), int(cl["fade_out_ms"]),
                           int(cl["crossfade_next_ms"])))

    @staticmethod
    def _core(doc):
        return {"name": doc["name"], "sample_rate": doc["sample_rate"], "tracks": doc["tracks"]}

    # ------------------------------------------------------------------ apply
    def apply(self, owner_id, pid, op: dict, expected_revision: int | None = None):
        """Apply one edit operation. Returns {doc, result}. 409 on stale revision or locked track."""
        if not isinstance(op, dict) or "type" not in op:
            raise VoiceLabError(422, "operation must be an object with a 'type'")
        with self.ctx.tx() as c:
            before = self.doc(pid, owner_id, c)
            if expected_revision is not None and expected_revision != before["revision"]:
                raise VoiceLabError(409, {"code": "STALE_REVISION", "current_revision": before["revision"]})
            work = copy.deepcopy(self._core(before))
            result = self._dispatch(owner_id, work, op, c)
            if op["type"] == "copy_range":  # read-only: not logged
                return {"doc": before, "result": result}
            rev = before["revision"] + 1
            c.execute("DELETE FROM vl_audio_edit_operations WHERE project_id=? AND undone=1", (pid,))  # drop redo branch
            seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM vl_audio_edit_operations WHERE project_id=?",
                            (pid,)).fetchone()[0]
            c.execute("INSERT INTO vl_audio_edit_operations VALUES(?,?,?,?,?,?,?,0,?)",
                      (new_id("op"), pid, seq, canon(op), canon(self._core(before)), canon(work), rev, now()))
            self._write(c, pid, work)
            c.execute("UPDATE vl_audio_projects SET name=?, sample_rate=?, revision=?, updated_at=? WHERE project_id=?",
                      (work["name"], work["sample_rate"], rev, now(), pid))
            return {"doc": self.doc(pid, owner_id, c), "result": result}

    def undo(self, owner_id, pid):
        return self._step(owner_id, pid, undo=True)

    def redo(self, owner_id, pid):
        return self._step(owner_id, pid, undo=False)

    def _step(self, owner_id, pid, undo):
        with self.ctx.tx() as c:
            cur = self.doc(pid, owner_id, c)
            q = ("SELECT * FROM vl_audio_edit_operations WHERE project_id=? AND undone=0 ORDER BY seq DESC LIMIT 1" if undo
                 else "SELECT * FROM vl_audio_edit_operations WHERE project_id=? AND undone=1 ORDER BY seq ASC LIMIT 1")
            r = c.execute(q, (pid,)).fetchone()
            if not r:
                raise VoiceLabError(409, "nothing to " + ("undo" if undo else "redo"))
            target = loads(r["doc_before_json"] if undo else r["doc_after_json"])
            c.execute("UPDATE vl_audio_edit_operations SET undone=? WHERE op_id=?", (1 if undo else 0, r["op_id"]))
            self._write(c, pid, target)
            c.execute("UPDATE vl_audio_projects SET name=?, sample_rate=?, revision=?, updated_at=? WHERE project_id=?",
                      (target["name"], target["sample_rate"], cur["revision"] + 1, now(), pid))
            return {"doc": self.doc(pid, owner_id, c), "result": {"op": loads(r["op_json"]), "undone" if undo else "redone": True}}

    def history(self, owner_id, pid):
        with closing(self.ctx.conn()) as c:
            self._project(c, pid, owner_id)
            return [{"seq": r["seq"], "op": loads(r["op_json"]), "revision_after": r["revision_after"],
                     "undone": bool(r["undone"]), "at": r["created_at"]}
                    for r in c.execute("SELECT * FROM vl_audio_edit_operations WHERE project_id=? ORDER BY seq", (pid,))]

    # ------------------------------------------------------------- operations
    def _track(self, doc, tid, *, allow_locked=False):
        t = next((t for t in doc["tracks"] if t["track_id"] == tid), None)
        if not t:
            raise VoiceLabError(404, f"track {tid} not found")
        if t["locked"] and not allow_locked:
            raise VoiceLabError(409, {"code": "TRACK_LOCKED", "track_id": tid})
        return t

    def _clip(self, doc, cid):
        for t in doc["tracks"]:
            for cl in t["clips"]:
                if cl["clip_id"] == cid:
                    if t["locked"]:
                        raise VoiceLabError(409, {"code": "TRACK_LOCKED", "track_id": t["track_id"]})
                    return t, cl
        raise VoiceLabError(404, f"clip {cid} not found")

    def _art_ms(self, owner_id, aid, conn=None):
        a = self.ctx.artifact(aid, owner_id, conn=conn)
        if not a["duration_ms"]:
            raise VoiceLabError(422, "artifact has no measured duration")
        return a["duration_ms"]

    @staticmethod
    def _num(v, name, lo=None, hi=None):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise VoiceLabError(422, f"{name} must be a number")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise VoiceLabError(422, f"{name} out of range [{lo}, {hi}]")
        return v

    def _validate_clip(self, owner_id, cl, conn=None):
        total = self._art_ms(owner_id, cl["artifact_id"], conn)
        for k in ("start_ms", "in_ms", "out_ms", "fade_in_ms", "fade_out_ms", "crossfade_next_ms"):
            self._num(cl[k], k, 0)
        self._num(cl["gain_db"], "gain_db", -60, 24)
        if not (0 <= cl["in_ms"] < cl["out_ms"] <= total):
            raise VoiceLabError(422, f"clip window must satisfy 0 <= in_ms < out_ms <= {total}")
        if cl["fade_in_ms"] + cl["fade_out_ms"] > _clip_len(cl):
            raise VoiceLabError(422, "fades longer than the clip")

    def _new_clip(self, owner_id, spec, conn=None):
        aid = spec.get("artifact_id")
        if not aid:
            raise VoiceLabError(422, "artifact_id is required")
        total = self._art_ms(owner_id, aid, conn)
        cl = {"clip_id": new_id("clip"), "artifact_id": aid, "start_ms": int(spec.get("start_ms", 0)),
              "in_ms": int(spec.get("in_ms", 0)), "out_ms": int(spec.get("out_ms", total)),
              "gain_db": float(spec.get("gain_db", 0)), "fade_in_ms": int(spec.get("fade_in_ms", 0)),
              "fade_out_ms": int(spec.get("fade_out_ms", 0)), "crossfade_next_ms": int(spec.get("crossfade_next_ms", 0))}
        self._validate_clip(owner_id, cl, conn)
        return cl

    def _cut(self, t, t0, t1, ripple):
        """Remove [t0, t1) from a track's timeline; returns removed pieces as clipboard items."""
        if t1 <= t0:
            raise VoiceLabError(422, "range end must be after start")
        keep, removed = [], []
        for cl in t["clips"]:
            s, e = cl["start_ms"], cl["start_ms"] + _clip_len(cl)
            if e <= t0 or s >= t1:
                keep.append(cl)
                continue
            if s < t0:  # left remainder
                left = dict(cl, out_ms=cl["in_ms"] + (t0 - s), fade_out_ms=min(cl["fade_out_ms"], t0 - s),
                            crossfade_next_ms=0)
                left["fade_in_ms"] = min(left["fade_in_ms"], _clip_len(left))
                keep.append(left)
            if e > t1:  # right remainder
                off = t1 - s
                right = dict(cl, clip_id=new_id("clip"), in_ms=cl["in_ms"] + off, start_ms=t1,
                             fade_in_ms=min(cl["fade_in_ms"], 0))
                right["fade_out_ms"] = min(right["fade_out_ms"], _clip_len(right))
                keep.append(right)
            a, b = max(s, t0), min(e, t1)
            removed.append({"artifact_id": cl["artifact_id"], "in_ms": cl["in_ms"] + (a - s),
                            "out_ms": cl["in_ms"] + (b - s), "offset_ms": a - t0, "gain_db": cl["gain_db"]})
        if ripple:
            for cl in keep:
                if cl["start_ms"] >= t1:
                    cl["start_ms"] -= (t1 - t0)
        t["clips"] = sorted(keep, key=lambda c: c["start_ms"])
        return removed

    def _dispatch(self, owner_id, doc, op, c):
        typ = op["type"]
        if typ == "set_project":
            if "name" in op:
                doc["name"] = str(op["name"]).strip()[:200] or doc["name"]
            if "sample_rate" in op:
                if op["sample_rate"] not in (16000, 22050, 24000, 44100, 48000):
                    raise VoiceLabError(422, "invalid sample_rate")
                doc["sample_rate"] = op["sample_rate"]
            return {}
        if typ == "add_track":
            role = op.get("role", "other")
            if role not in ROLES:
                raise VoiceLabError(422, f"role must be one of {sorted(ROLES)}")
            t = {"track_id": new_id("trk"), "name": str(op.get("name") or role)[:120], "role": role,
                 "gain_db": float(self._num(op.get("gain_db", 0), "gain_db", -60, 24)), "mute": False, "solo": False,
                 "locked": False, "effects": [], "clips": []}
            doc["tracks"].append(t)
            return {"track_id": t["track_id"]}
        if typ == "update_track":
            t = self._track(doc, op.get("track_id"), allow_locked=True)
            bad = set(op) - TRACK_FIELDS - {"type", "track_id"}
            if bad:
                raise VoiceLabError(422, f"unknown track fields {sorted(bad)}")
            if t["locked"] and set(op) - {"type", "track_id", "locked", "mute", "solo"}:
                raise VoiceLabError(409, {"code": "TRACK_LOCKED", "track_id": t["track_id"]})
            if "role" in op and op["role"] not in ROLES:
                raise VoiceLabError(422, "invalid role")
            if "gain_db" in op:
                self._num(op["gain_db"], "gain_db", -60, 24)
            if "effects" in op:
                self._validate_effects(op["effects"])
            for k in TRACK_FIELDS & set(op):
                t[k] = op[k] if k not in ("mute", "solo", "locked") else bool(op[k])
            return {}
        if typ == "remove_track":
            self._track(doc, op.get("track_id"))
            doc["tracks"] = [t for t in doc["tracks"] if t["track_id"] != op["track_id"]]
            return {}
        if typ == "move_track":
            t = self._track(doc, op.get("track_id"), allow_locked=True)
            idx = int(self._num(op.get("index"), "index", 0, len(doc["tracks"]) - 1))
            doc["tracks"].remove(t)
            doc["tracks"].insert(idx, t)
            return {}
        if typ == "add_clip":
            t = self._track(doc, op.get("track_id"))
            cl = self._new_clip(owner_id, op)
            t["clips"].append(cl)
            t["clips"].sort(key=lambda x: x["start_ms"])
            return {"clip_id": cl["clip_id"]}
        if typ == "update_clip":
            t, cl = self._clip(doc, op.get("clip_id"))
            bad = set(op) - CLIP_FIELDS - {"type", "clip_id"}
            if bad:
                raise VoiceLabError(422, f"unknown clip fields {sorted(bad)}")
            new = dict(cl)
            for k in CLIP_FIELDS - {"track_id"}:
                if k in op:
                    new[k] = op[k]
            self._validate_clip(owner_id, new)
            cl.update(new)
            if op.get("track_id") and op["track_id"] != t["track_id"]:
                dest = self._track(doc, op["track_id"])
                t["clips"].remove(cl)
                dest["clips"].append(cl)
                dest["clips"].sort(key=lambda x: x["start_ms"])
            t["clips"].sort(key=lambda x: x["start_ms"])
            return {}
        if typ == "split_clip":
            t, cl = self._clip(doc, op.get("clip_id"))
            at = int(self._num(op.get("at_ms"), "at_ms"))
            s, e = cl["start_ms"], cl["start_ms"] + _clip_len(cl)
            if not s < at < e:
                raise VoiceLabError(422, "split point must be inside the clip")
            right = dict(cl, clip_id=new_id("clip"), in_ms=cl["in_ms"] + (at - s), start_ms=at, fade_in_ms=0)
            right["fade_out_ms"] = min(cl["fade_out_ms"], _clip_len(right))
            cl.update(out_ms=cl["in_ms"] + (at - s), fade_out_ms=0, crossfade_next_ms=0)
            cl["fade_in_ms"] = min(cl["fade_in_ms"], _clip_len(cl))
            t["clips"].append(right)
            t["clips"].sort(key=lambda x: x["start_ms"])
            return {"left_clip_id": cl["clip_id"], "right_clip_id": right["clip_id"]}
        if typ == "delete_clip":
            t, cl = self._clip(doc, op.get("clip_id"))
            t["clips"].remove(cl)
            return {}
        if typ == "duplicate_clip":
            t, cl = self._clip(doc, op.get("clip_id"))
            dest = self._track(doc, op.get("track_id") or t["track_id"])
            dup = dict(cl, clip_id=new_id("clip"), start_ms=int(op.get("start_ms", cl["start_ms"] + _clip_len(cl))))
            self._validate_clip(owner_id, dup)
            dest["clips"].append(dup)
            dest["clips"].sort(key=lambda x: x["start_ms"])
            return {"clip_id": dup["clip_id"]}
        if typ in ("cut_range", "silence_range"):
            t = self._track(doc, op.get("track_id"))
            removed = self._cut(t, int(op["t0_ms"]), int(op["t1_ms"]), bool(op.get("ripple")) and typ == "cut_range")
            return {"clipboard": removed}
        if typ == "copy_range":
            t = self._track(doc, op.get("track_id"), allow_locked=True)
            tmp = copy.deepcopy(t)
            return {"clipboard": self._cut(tmp, int(op["t0_ms"]), int(op["t1_ms"]), False)}
        if typ == "paste":
            t = self._track(doc, op.get("track_id"))
            at = int(self._num(op.get("at_ms"), "at_ms", 0))
            items = op.get("clipboard") or []
            if not items:
                raise VoiceLabError(422, "clipboard is empty")
            span = max(i["offset_ms"] + i["out_ms"] - i["in_ms"] for i in items)
            if op.get("ripple"):
                for cl in t["clips"]:
                    if cl["start_ms"] >= at:
                        cl["start_ms"] += span
            ids = []
            for it in items:
                cl = self._new_clip(owner_id, {"artifact_id": it["artifact_id"], "in_ms": it["in_ms"],
                                               "out_ms": it["out_ms"], "start_ms": at + int(it.get("offset_ms", 0)),
                                               "gain_db": it.get("gain_db", 0)})
                t["clips"].append(cl)
                ids.append(cl["clip_id"])
            t["clips"].sort(key=lambda x: x["start_ms"])
            return {"clip_ids": ids}
        if typ == "insert_silence":
            t = self._track(doc, op.get("track_id"))
            at = int(self._num(op.get("at_ms"), "at_ms", 0))
            dur = int(self._num(op.get("duration_ms"), "duration_ms", 1, 600000))
            for cl in list(t["clips"]):
                s, e = cl["start_ms"], cl["start_ms"] + _clip_len(cl)
                if s < at < e:  # split the clip that spans the insertion point
                    right = dict(cl, clip_id=new_id("clip"), in_ms=cl["in_ms"] + (at - s), start_ms=at, fade_in_ms=0)
                    cl.update(out_ms=cl["in_ms"] + (at - s), fade_out_ms=0)
                    t["clips"].append(right)
            for cl in t["clips"]:
                if cl["start_ms"] >= at:
                    cl["start_ms"] += dur
            t["clips"].sort(key=lambda x: x["start_ms"])
            return {}
        if typ == "join_clips":
            ids = op.get("clip_ids") or []
            if len(ids) < 2:
                raise VoiceLabError(422, "join needs at least two clip_ids")
            pairs = [self._clip(doc, i) for i in ids]
            t = pairs[0][0]
            if any(p[0] is not t for p in pairs):
                raise VoiceLabError(422, "clips must be on the same track")
            clips = sorted((p[1] for p in pairs), key=lambda x: x["start_ms"])
            contiguous = all(a["artifact_id"] == b["artifact_id"] and a["out_ms"] == b["in_ms"] and
                             a["start_ms"] + _clip_len(a) == b["start_ms"] for a, b in zip(clips, clips[1:]))
            if contiguous:
                first = clips[0]
                first.update(out_ms=clips[-1]["out_ms"], fade_out_ms=clips[-1]["fade_out_ms"],
                             crossfade_next_ms=clips[-1]["crossfade_next_ms"])
                for cl in clips[1:]:
                    t["clips"].remove(cl)
                return {"clip_id": first["clip_id"], "mode": "MERGED_WINDOWS"}
            # Different sources: render the selection to a new artifact (originals untouched).
            start = clips[0]["start_ms"]
            sub = {"name": doc["name"], "sample_rate": doc["sample_rate"],
                   "tracks": [dict(t, mute=False, solo=False, gain_db=0.0, effects=[], clips=clips)]}
            y = self._mix(owner_id, sub, channels=1)
            y = y[int(start * doc["sample_rate"] / 1000):]
            art = self.ctx.register_audio(owner_id, y, doc["sample_rate"], kind="PROJECT_UPLOAD",
                                          source={"joined_from": [cl["clip_id"] for cl in clips],
                                                  "artifacts": sorted({cl["artifact_id"] for cl in clips})}, conn=c)
            for cl in clips:
                t["clips"].remove(cl)
            new = self._new_clip(owner_id, {"artifact_id": art["artifact_id"], "start_ms": start}, conn=c)
            t["clips"].append(new)
            t["clips"].sort(key=lambda x: x["start_ms"])
            return {"clip_id": new["clip_id"], "mode": "RENDERED_JOIN", "artifact_id": art["artifact_id"]}
        raise VoiceLabError(422, f"unknown operation type {typ!r}")

    @staticmethod
    def _validate_effects(effects):
        if not isinstance(effects, list) or len(effects) > 8:
            raise VoiceLabError(422, "effects must be a list (max 8)")
        for e in effects:
            if not isinstance(e, dict) or e.get("type") not in EFFECTS:
                raise VoiceLabError(422, f"effect type must be one of {sorted(EFFECTS)}")
            if e["type"] == "eq":
                if not (20 <= e.get("freq_hz", 0) <= 20000 and -24 <= e.get("gain_db", 99) <= 24 and 0.1 <= e.get("q", 1) <= 10):
                    raise VoiceLabError(422, "eq needs freq_hz 20..20000, gain_db -24..24, q 0.1..10")
            if e["type"] in ("highpass", "lowpass") and not 20 <= e.get("freq_hz", 0) <= 20000:
                raise VoiceLabError(422, f"{e['type']} needs freq_hz 20..20000")
            if e["type"] == "denoise" and not 1 <= e.get("reduction_db", 12) <= 40:
                raise VoiceLabError(422, "denoise reduction_db 1..40")

    @staticmethod
    def _filtergraph(effects):
        parts = []
        for e in effects or []:
            if e["type"] == "eq":
                parts.append(f"equalizer=f={e['freq_hz']}:t=q:w={e.get('q', 1)}:g={e['gain_db']}")
            elif e["type"] == "highpass":
                parts.append(f"highpass=f={e['freq_hz']}")
            elif e["type"] == "lowpass":
                parts.append(f"lowpass=f={e['freq_hz']}")
            elif e["type"] == "denoise":
                parts.append(f"afftdn=nr={e.get('reduction_db', 12)}")
        return ",".join(parts)

    # ------------------------------------------------------------------ render
    def _mix(self, owner_id, doc, *, channels=1, t0_ms=0, t1_ms=None):
        sr = doc["sample_rate"]
        dur = max([cl["start_ms"] + _clip_len(cl) for t in doc["tracks"] for cl in t["clips"]] or [0])
        end = t1_ms if t1_ms is not None else dur
        n = max(1, int((end - t0_ms) * sr / 1000))
        out = np.zeros(n, np.float32)
        cache: dict[str, np.ndarray] = {}
        any_solo = any(t["solo"] for t in doc["tracks"])
        for t in doc["tracks"]:
            if t["mute"] or (any_solo and not t["solo"]):
                continue
            bus = np.zeros(n, np.float32)
            clips = sorted(t["clips"], key=lambda c: c["start_ms"])
            for i, cl in enumerate(clips):
                if cl["artifact_id"] not in cache:
                    cache[cl["artifact_id"]] = A.decode(self.ctx.artifact_path(cl["artifact_id"], owner_id), sr)
                src = cache[cl["artifact_id"]]
                seg = src[int(cl["in_ms"] * sr / 1000):int(cl["out_ms"] * sr / 1000)].copy()
                if seg.size == 0:
                    continue
                seg *= 10 ** (cl["gain_db"] / 20)
                fi = int(cl["fade_in_ms"] * sr / 1000)
                prev_x = clips[i - 1]["crossfade_next_ms"] if i else 0
                fi = max(fi, int(prev_x * sr / 1000))
                fo = max(int(cl["fade_out_ms"] * sr / 1000), int(cl["crossfade_next_ms"] * sr / 1000))
                if fi:
                    fi = min(fi, seg.size); seg[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)
                if fo:
                    fo = min(fo, seg.size); seg[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
                pos = int((cl["start_ms"] - t0_ms) * sr / 1000)
                a, b = max(0, pos), min(n, pos + seg.size)
                if b > a:
                    bus[a:b] += seg[a - pos:b - pos]
            fg = self._filtergraph(t.get("effects"))
            if fg and np.any(bus):
                bus = A.run_filter(bus, sr, fg)[:n]
            out[:len(bus)] += bus * 10 ** (t["gain_db"] / 20)
        if channels == 2:
            return np.stack([out, out], axis=1)
        return out

    def export(self, owner_id, pid, *, fmt="wav", sample_rate=None, channels=1, normalize_lufs=None, t0_ms=0,
               t1_ms=None, expected_revision=None):
        doc = self.doc(pid, owner_id)
        if expected_revision is not None and expected_revision != doc["revision"]:
            raise VoiceLabError(409, {"code": "STALE_REVISION", "current_revision": doc["revision"]})
        if not any(t["clips"] for t in doc["tracks"]):
            raise VoiceLabError(409, "project has no clips")
        if channels not in (1, 2):
            raise VoiceLabError(422, "channels must be 1 or 2")
        if fmt not in A.EXPORT_FORMATS:
            raise VoiceLabError(422, f"format must be one of {sorted(A.EXPORT_FORMATS)}")
        work = dict(doc)
        y = self._mix(owner_id, work, channels=channels, t0_ms=t0_ms, t1_ms=t1_ms)
        sr = doc["sample_rate"]
        if sample_rate and sample_rate != sr:
            y = self._resample(y, sr, sample_rate)
            sr = sample_rate
        before = A.loudness(y, sr)
        applied = 0.0
        if normalize_lufs is not None:
            if not -40 <= normalize_lufs <= -5:
                raise VoiceLabError(422, "normalize_lufs must be -40..-5")
            if before["integrated_lufs"] is None:
                raise VoiceLabError(409, "export is silent; cannot normalise")
            applied = normalize_lufs - before["integrated_lufs"]
            y = y * 10 ** (applied / 20)
        peak = float(np.abs(y).max()) if y.size else 0.0
        limited = peak > 0.891  # -1 dBFS sample-peak ceiling
        if limited:
            y = y * (0.891 / peak)
        after = A.loudness(y, sr)
        out = self.ctx.tmp_path(f".{fmt}")
        try:
            A.encode(y, sr, fmt, out)
            check = A.decode(out, sr, channels=channels)
            n_out = check.shape[0]
            art = self.ctx.register_file(owner_id, out, kind="EXPORT", fmt=fmt, move=True, sample_rate=sr,
                                         channels=channels, duration_ms=int(n_out * 1000 / sr),
                                         source={"project_id": pid, "revision": doc["revision"],
                                                 "doc_sha256": sha256_bytes(canon(self._core(doc)).encode()),
                                                 "range_ms": [t0_ms, t1_ms]})
        finally:
            out.unlink(missing_ok=True)
        expected_ms = int(y.shape[0] * 1000 / sr)
        return {"artifact": art, "url": f"/api/voice-lab/artifacts/{art['artifact_id']}/audio",
                "integrity": {"decoded_duration_ms": art["duration_ms"], "expected_duration_ms": expected_ms,
                              "duration_match": abs(art["duration_ms"] - expected_ms) <= (60 if fmt in ("mp3", "ogg") else 2),
                              "sha256": art["sha256"]},
                "loudness_before": before, "loudness_after": after, "normalization_gain_db": round(applied, 2),
                "peak_limited": limited}

    @staticmethod
    def _resample(y, sr, target):
        n = int(round(y.shape[0] * target / sr))
        xs = np.linspace(0, y.shape[0] - 1, n)
        if y.ndim == 1:
            return np.interp(xs, np.arange(y.shape[0]), y).astype(np.float32)
        return np.stack([np.interp(xs, np.arange(y.shape[0]), y[:, i]) for i in range(y.shape[1])], 1).astype(np.float32)
