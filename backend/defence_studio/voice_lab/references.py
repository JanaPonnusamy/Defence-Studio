"""Reference media, delivery analysis and reusable style profiles (VL-5, VL-6 storage).

A reference is analysed for DELIVERY only. Its audio is never used to train or
condition a narrator, and no speaker embedding is computed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import closing
from pathlib import Path

import numpy as np

from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab import prosody as P
from defence_studio.voice_lab import style as S
from defence_studio.voice_lab.common import VoiceLabError, canon, loads, new_id, now, sha256_bytes
from defence_studio.voice_lab.context import Ctx

SEPARATION = {"status": "NOT_AVAILABLE",
              "detail": "No licence-cleared source separator is installed; non-speech sound is flagged, not removed. "
                        "Use clean narration references for best results."}


class References:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    def create(self, owner_id, *, name, authorization_ref, temp_path: Path | None = None, original_name="",
               source_url=None):
        if not (authorization_ref or "").strip():
            raise VoiceLabError(422, "authorization_ref is required: confirm you are authorised to analyse this media")
        if not (name or "").strip():
            raise VoiceLabError(422, "name is required")
        rid, ts = new_id("ref"), now()
        art = None
        if temp_path is not None:
            ext = Path(original_name or temp_path.name).suffix.lower()
            if ext not in A.SUPPORTED_EXT:
                raise VoiceLabError(422, f"unsupported media format {ext or '(none)'}")
            if not A.probe_has_audio(temp_path):
                raise VoiceLabError(422, "file has no decodable audio stream")
            art = self.ctx.register_file(owner_id, temp_path, kind="REFERENCE_SOURCE", fmt=ext.lstrip("."), move=True,
                                         source={"reference_id": rid, "original_name": original_name})
            kind = "UPLOAD"
        elif source_url:
            if not source_url.startswith(("https://", "http://")):
                raise VoiceLabError(422, "source_url must be http(s)")
            if not shutil.which("yt-dlp"):
                raise VoiceLabError(409, {"code": "URL_IMPORT_UNAVAILABLE",
                                          "detail": "yt-dlp is not installed; download the authorised media yourself and upload it"})
            kind = "URL"
        else:
            raise VoiceLabError(422, "upload a file or give source_url")
        with self.ctx.tx() as c:
            c.execute("INSERT INTO vl_reference_media VALUES(?,?,?,?,?,?,?,NULL,?,NULL,'QUEUED',NULL,?,?)",
                      (rid, owner_id, name.strip(), kind, source_url, authorization_ref.strip(),
                       art["artifact_id"] if art else None, art["sha256"] if art else None, ts, ts))
        return self.get(rid, owner_id)

    def get(self, rid, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT * FROM vl_reference_media WHERE reference_id=? AND owner_id=?", (rid, owner_id)).fetchone()
            if not r:
                raise VoiceLabError(404, "reference not found")
            an = c.execute("SELECT analysis_id, version, analysis_hash, summary_json, quality_json, created_at FROM "
                           "vl_reference_analyses WHERE reference_id=? ORDER BY version DESC LIMIT 1", (rid,)).fetchone()
        d = dict(r)
        d["separation"] = SEPARATION
        d["latest_analysis"] = (dict(an) | {"summary": loads(an["summary_json"]), "quality": loads(an["quality_json"])}
                                if an else None)
        if d["latest_analysis"]:
            d["latest_analysis"].pop("summary_json"); d["latest_analysis"].pop("quality_json")
        return d

    def list(self, owner_id):
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute("SELECT reference_id FROM vl_reference_media WHERE owner_id=? ORDER BY created_at",
                                           (owner_id,))]
        return [self.get(i, owner_id) for i in ids]

    def _set(self, rid, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.ctx.tx() as c:
            c.execute(f"UPDATE vl_reference_media SET {sets}, updated_at=? WHERE reference_id=?", (*cols.values(), now(), rid))

    def _download(self, owner_id, ref):
        out = self.ctx.tmp_path("")
        out.mkdir(parents=True)
        r = subprocess.run(["yt-dlp", "--no-playlist", "-f", "bestaudio/best", "-o", str(out / "media.%(ext)s"),
                            ref["source_url"]], capture_output=True, text=True, timeout=1800)
        files = list(out.glob("media.*"))
        if r.returncode != 0 or not files:
            shutil.rmtree(out, ignore_errors=True)
            raise RuntimeError("download failed: " + (r.stderr or "")[-300:])
        art = self.ctx.register_file(owner_id, files[0], kind="REFERENCE_SOURCE", fmt=files[0].suffix.lstrip("."),
                                     move=True, source={"reference_id": ref["reference_id"], "url": ref["source_url"]})
        shutil.rmtree(out, ignore_errors=True)
        self._set(ref["reference_id"], source_artifact_id=art["artifact_id"], sha256=art["sha256"])
        return art["artifact_id"]

    def analyse(self, owner_id, rid, should_abort=lambda: False):
        ref = self.get(rid, owner_id)
        self._set(rid, status="RUNNING", error=None)
        try:
            src_id = ref["source_artifact_id"] or self._download(owner_id, ref)
            src = self.ctx.artifact_path(src_id, owner_id, verify=True)
            x16 = A.decode(src, P.SR)
            speech = self.ctx.register_audio(owner_id, x16, P.SR, kind="REFERENCE_SPEECH",
                                             source={"reference_id": rid, "from_artifact": src_id,
                                                     "separation": SEPARATION["status"]})
            if should_abort():
                raise RuntimeError("cancelled")
            an = P.analyse_signal(x16)
            if not an["phrases"]:
                raise VoiceLabError(422, {"code": "NO_SPEECH", "quality": an["quality"]})
            regions = P.classify_regions(x16, an["phrases"], an["total_ms"])
            emo = P.emotion_estimate(an["summary"])
            frames = {"hop_ms": P.HOP_MS, "sample_rate": P.SR,
                      "f0_hz": [None if np.isnan(v) else round(float(v), 1) for v in an["frames"]["f0_hz"]],
                      "energy_db": [round(float(v), 1) for v in an["frames"]["energy_db"]]}
            fdata = json.dumps(frames, separators=(",", ":")).encode()
            fart = self.ctx.register_bytes(owner_id, fdata, kind="ANALYSIS_FRAMES", fmt="json", ext=".json",
                                           source={"reference_id": rid})
            content = {"version": P.PROSODY_VERSION, "summary": an["summary"], "phrases": an["phrases"],
                       "frames_sha256": fart["sha256"]}
            ahash = sha256_bytes(canon(content).encode())
            aid = new_id("ana")
            with self.ctx.tx() as c:
                ver = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM vl_reference_analyses WHERE reference_id=?",
                                (rid,)).fetchone()[0]
                c.execute("INSERT INTO vl_reference_analyses VALUES(?,?,?,?,?,?,?,?,?)",
                          (aid, rid, ver, P.PROSODY_VERSION, ahash, fart["rel_path"],
                           canon(an["summary"] | {"emotion": emo, "separation": SEPARATION["status"]}),
                           canon(an["quality"]), now()))
                c.executemany("INSERT INTO vl_reference_segments VALUES(?,?,?,?,?,?,?)",
                              [(new_id("rseg"), aid, i, g["t0_ms"], g["t1_ms"], g["classification"], g["confidence"])
                               for i, g in enumerate(regions)])
                c.executemany("INSERT INTO vl_prosody_segments VALUES(?,?,?,?,?,?)",
                              [(new_id("pros"), aid, p["ordinal"], p["t0_ms"], p["t1_ms"], canon(p)) for p in an["phrases"]])
            self._set(rid, status="ANALYSED", speech_artifact_id=speech["artifact_id"], duration_ms=an["total_ms"])
            return aid
        except Exception as exc:
            self._set(rid, status="FAILED", error=(str(exc.detail) if isinstance(exc, VoiceLabError) else str(exc))[:1000])
            raise

    def analysis(self, owner_id, rid, analysis_id=None, include_frames=False):
        self.get(rid, owner_id)
        with closing(self.ctx.conn()) as c:
            q = "SELECT * FROM vl_reference_analyses WHERE reference_id=?" + (" AND analysis_id=?" if analysis_id else "")
            r = c.execute(q + " ORDER BY version DESC LIMIT 1", (rid, analysis_id) if analysis_id else (rid,)).fetchone()
            if not r:
                raise VoiceLabError(404, "no analysis yet")
            segs = [dict(x) for x in c.execute("SELECT t0_ms, t1_ms, classification, confidence FROM vl_reference_segments "
                                               "WHERE analysis_id=? ORDER BY ordinal", (r["analysis_id"],))]
            pros = [loads(x[0]) for x in c.execute("SELECT features_json FROM vl_prosody_segments WHERE analysis_id=? "
                                                   "ORDER BY ordinal", (r["analysis_id"],))]
        d = dict(r)
        d["summary"], d["quality"] = loads(d.pop("summary_json")), loads(d.pop("quality_json"))
        d["regions"], d["phrases"] = segs, pros
        d["separation"] = SEPARATION
        if include_frames:
            p = self.ctx.storage_root / d["frames_rel_path"]
            d["frames"] = json.loads(p.read_text()) if p.is_file() else None
        return d

    # ------------------------------------------------------------ style profiles
    def create_style(self, owner_id, *, reference_id, name, analysis_id=None):
        an = self.analysis(owner_id, reference_id, analysis_id)
        feats = S.profile_features({"summary": an["summary"], "phrases": an["phrases"], "total_ms": an["summary"]["total_ms"]})
        emo = an["summary"].get("emotion")
        rows = {
            "timing": ({"total_ms": feats["total_ms"], "leading_silence_ms": feats["leading_silence_ms"],
                        "trailing_silence_ms": feats["trailing_silence_ms"],
                        "phrases": [{k: p[k] for k in ("duration_ms", "pause_after_ms")} for p in feats["phrases"]]},
                       "MEASURED"),
            "pitch_relative": ([{k: p[k] for k in ("rel_median_st", "range_st", "slope_st", "intonation")}
                                for p in feats["phrases"]], "MEASURED"),
            "energy": ([p["energy_rel_db"] for p in feats["phrases"]], "MEASURED"),
            "emphasis": ([p["emphasis_rel_ms"] for p in feats["phrases"]], "MEASURED"),
            "rate": ([p["rate_syl_per_s_estimate"] for p in feats["phrases"]], "ESTIMATED"),
            "global": (feats["global"], "MEASURED"),
            "emotion": (emo, "INFERENCE"),
            "delivery_label": ({"label": None, "notes": ""}, "HUMAN_PENDING"),
            "provenance_only": (feats["provenance_only"], "MEASURED"),
        }
        sid, ts = new_id("style"), now()
        fsha = sha256_bytes(canon({k: v[0] for k, v in rows.items()}).encode())
        with self.ctx.tx() as c:
            c.execute("INSERT INTO vl_style_profiles VALUES(?,?,?,?,?,?,'DRAFT',NULL,NULL,?,?)",
                      (sid, owner_id, name.strip(), reference_id, an["analysis_id"], fsha, ts, ts))
            c.executemany("INSERT INTO vl_style_features VALUES(?,?,?,?,?,?,?,NULL,NULL)",
                          [(new_id("feat"), sid, k, canon(v), 0, feats["total_ms"], kind) for k, (v, kind) in rows.items()])
        return self.style(sid, owner_id)

    def style(self, sid, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT * FROM vl_style_profiles WHERE style_profile_id=? AND owner_id=?", (sid, owner_id)).fetchone()
            if not r:
                raise VoiceLabError(404, "style profile not found")
            fs = c.execute("SELECT feature_key, value_json, label_kind, reviewed_by, reviewed_at, source_t0_ms, source_t1_ms "
                           "FROM vl_style_features WHERE style_profile_id=?", (sid,)).fetchall()
        d = dict(r)
        d["features"] = {f["feature_key"]: {"value": loads(f["value_json"]), "label_kind": f["label_kind"],
                                            "reviewed_by": f["reviewed_by"], "reviewed_at": f["reviewed_at"],
                                            "source_span_ms": [f["source_t0_ms"], f["source_t1_ms"]]} for f in fs}
        d["identity_note"] = "Delivery features only (relative pitch, timing, energy). No speaker identity is stored."
        return d

    def list_styles(self, owner_id):
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute("SELECT style_profile_id FROM vl_style_profiles WHERE owner_id=? ORDER BY created_at",
                                           (owner_id,))]
        return [self.style(i, owner_id) for i in ids]

    def review_label(self, owner_id, sid, reviewer, *, feature_key, value):
        st = self.style(sid, owner_id)
        if st["status"] == "APPROVED":
            raise VoiceLabError(409, "approved style profiles are immutable; create a new profile")
        if feature_key not in ("emotion", "delivery_label"):
            raise VoiceLabError(422, "only 'emotion' and 'delivery_label' accept human labels; measured features are not editable")
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_style_features SET value_json=?, label_kind='HUMAN', reviewed_by=?, reviewed_at=? "
                      "WHERE style_profile_id=? AND feature_key=?", (canon(value), reviewer, now(), sid, feature_key))
            vals = {k: loads(v) for k, v in c.execute("SELECT feature_key, value_json FROM vl_style_features "
                                                     "WHERE style_profile_id=?", (sid,))}
            c.execute("UPDATE vl_style_profiles SET features_sha256=?, updated_at=? WHERE style_profile_id=?",
                      (sha256_bytes(canon(vals).encode()), now(), sid))
        return self.style(sid, owner_id)

    def approve_style(self, owner_id, sid, approver):
        self.style(sid, owner_id)
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_style_profiles SET status='APPROVED', approved_by=?, approved_at=?, updated_at=? "
                      "WHERE style_profile_id=?", (approver, now(), now(), sid))
        return self.style(sid, owner_id)

    def transfer_features(self, owner_id, sid) -> dict:
        """Assemble the feature dict consumed by style.apply_style (reused without re-analysis)."""
        st = self.style(sid, owner_id)
        f = {k: v["value"] for k, v in st["features"].items()}
        t = f["timing"]
        phrases = []
        for i, ph in enumerate(t["phrases"]):
            pr = f["pitch_relative"][i]
            phrases.append({**ph, **pr, "energy_rel_db": f["energy"][i], "emphasis_rel_ms": f["emphasis"][i],
                            "rate_syl_per_s_estimate": f["rate"][i]})
        return {"style_version": S.STYLE_VERSION, "total_ms": t["total_ms"], "leading_silence_ms": t["leading_silence_ms"],
                "trailing_silence_ms": t["trailing_silence_ms"], "phrases": phrases, "global": f["global"],
                "style_profile_id": sid, "features_sha256": st["features_sha256"], "status": st["status"]}
