"""Narration generation with optional reference-delivery matching, and the Script Studio contract.

* The narrator's own model version always produces the speech (identity check on every request).
* A style profile only re-times / re-shapes that output (style.apply_style).
* Requests are content-keyed: identical paragraph + narrator version + style + settings
  returns the existing artifact (no regeneration of unchanged paragraphs) unless forced.
* No fallback voice: if the model is not installed the request fails with 409.
* Tamil text goes through the production preparation (services/tts_text.py: reviewed lexicon,
  designation table, unit rules). Only the text sent to the model changes; the approved text
  stored on the job (and used for subtitles) stays exactly as submitted. Other languages pass unchanged.
"""
from __future__ import annotations

import re
import unicodedata
from contextlib import closing

import numpy as np

from defence_studio.services import tts_text
from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab import style as S
from defence_studio.voice_lab.common import VoiceLabError, canon, loads, new_id, now, sha256_bytes
from defence_studio.voice_lab.context import Ctx
from defence_studio.voice_lab.inference import install_status
from defence_studio.voice_lab.narrators import Narrators
from defence_studio.voice_lab.references import References

OUT_SR = 48000
MAX_CHARS = 4000
CHUNK_CHARS = 260
_SENT = re.compile(r"(?<=[.!?।॥])\s+")


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    parts = [p.strip() for p in _SENT.split(text) if p.strip()]
    out, cur = [], ""
    for p in parts:
        while len(p) > limit:  # a single over-long sentence: cut at the last comma/space before the limit
            cut = max(p.rfind(",", 0, limit), p.rfind(" ", 0, limit))
            cut = cut if cut > limit // 3 else limit
            out.append(p[:cut + 1].strip()); p = p[cut + 1:].strip()
        if cur and len(cur) + 1 + len(p) > limit:
            out.append(cur); cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def prepare_text(text: str, language: str) -> tuple[str, dict | None]:
    """(spoken text for the model, preparation record). The record is None for non-Tamil text, which is
    passed through unchanged. Broken Tamil text is refused (422), never repaired silently."""
    try:
        p = tts_text.prepare(text, language)
    except tts_text.TamilTextError as exc:
        raise VoiceLabError(422, {"code": "TAMIL_TEXT_INVALID", "detail": str(exc)}) from exc
    if language != "ta":
        return p["spoken"], None
    return p["spoken"], {"spoken": p["spoken"], "spoken_sha256": sha256_bytes(p["spoken"].encode()),
                         "replacements": p["replacements"], "warnings": p["warnings"],
                         "lexicon_sha256": p["lexicon_sha256"], "source": "services/tts_text.py"}


class Generation:
    def __init__(self, ctx: Ctx, narrators: Narrators, references: References, backends: dict):
        self.ctx, self.narr, self.refs, self.backends = ctx, narrators, references, backends

    def create(self, owner_id, *, narrator_id, text, language="ta", model_version_id=None, style_profile_id=None,
               target_duration_ms=None, config=None, script_ref=None, force=False, enqueue):
        cfg = dict(config or {})
        n, v = self.narr.resolve_version(narrator_id, owner_id, model_version_id)
        text = unicodedata.normalize("NFC", (text or "").strip())
        if not text:
            raise VoiceLabError(422, "text is empty")
        if len(text) > MAX_CHARS:
            raise VoiceLabError(413, f"text longer than {MAX_CHARS} characters; send one paragraph per request")
        if language not in n["languages"]:
            raise VoiceLabError(422, f"narrator {narrator_id} does not list language {language}")
        model = self.ctx.model(v["model_id"])
        if language not in model.get("languages", []):
            raise VoiceLabError(422, f"model {v['model_id']} does not support language {language}")
        if model.get("runtime") not in self.backends:
            raise VoiceLabError(409, {"code": "NO_RUNTIME", "detail": f"no inference runtime for {v['model_id']}"})
        status = install_status(model) if not getattr(self.backends[model["runtime"]], "skip_install_check", False) \
            else {"installed": True}
        if not status["installed"]:
            raise VoiceLabError(409, {"code": "MODEL_NOT_INSTALLED", "model_id": v["model_id"], "status": status})
        style_sha = None
        if style_profile_id:
            st = self.refs.style(style_profile_id, owner_id)
            if st["status"] != "APPROVED" and not cfg.get("allow_draft_style"):
                raise VoiceLabError(409, {"code": "STYLE_NOT_APPROVED", "detail": "approve the style profile first"})
            style_sha = st["features_sha256"]
        if target_duration_ms is not None and not (500 <= int(target_duration_ms) <= 3_600_000):
            raise VoiceLabError(422, "target_duration_ms must be 500..3600000")
        _, prep = prepare_text(text, language)
        cfg.setdefault("seed", 1234)
        key_src = {"narrator_id": narrator_id, "model_version_id": v["model_version_id"], "style": style_profile_id,
                   "style_sha": style_sha, "text": text, "language": language, "target": target_duration_ms, "config": cfg}
        if prep:  # a lexicon/designation change alters the spoken form, so it must not reuse old audio
            key_src["spoken_sha256"] = prep["spoken_sha256"]
        rkey = sha256_bytes(canon(key_src).encode())
        if not force:
            with closing(self.ctx.conn()) as c:
                ex = c.execute("SELECT generation_id FROM vl_generation_jobs WHERE owner_id=? AND request_key=? AND "
                               "status IN ('QUEUED','RUNNING','COMPLETED') ORDER BY created_at DESC LIMIT 1",
                               (owner_id, rkey)).fetchone()
            if ex:
                return self.get(ex[0], owner_id) | {"reused": True, "text_preparation": prep}
        est = len(text) * 48000 * 2 // 10 + 10 * 1024 ** 2  # rough WAV size bound for the disk guard
        self.ctx.require_space(est)
        gid, ts = new_id("gen"), now()
        with self.ctx.tx() as c:
            c.execute("INSERT INTO vl_generation_jobs(generation_id, owner_id, request_key, narrator_id, model_version_id, "
                      "style_profile_id, language, text, text_sha256, target_duration_ms, config_json, script_ref_json, "
                      "status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'QUEUED',?,?)",
                      (gid, owner_id, rkey, narrator_id, v["model_version_id"], style_profile_id, language, text,
                       sha256_bytes(text.encode()), target_duration_ms, canon(cfg),
                       canon(script_ref) if script_ref else None, ts, ts))
        wf = enqueue(owner_id, f"vl-gen-{gid}", "vl_generation", gid, f"Voice Lab generation {narrator_id}")
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_generation_jobs SET workflow_job_id=? WHERE generation_id=?", (wf, gid))
        return self.get(gid, owner_id) | {"reused": False, "text_preparation": prep}

    def get(self, gid, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT * FROM vl_generation_jobs WHERE generation_id=? AND owner_id=?", (gid, owner_id)).fetchone()
        if not r:
            raise VoiceLabError(404, "generation not found")
        d = dict(r)
        for k in ("config", "script_ref", "timing", "provenance"):
            d[k] = loads(d.pop(f"{k}_json"), None)
        if d["artifact_id"]:
            a = self.ctx.artifact(d["artifact_id"], owner_id)
            d["audio"] = {"artifact_id": a["artifact_id"], "url": f"/api/voice-lab/artifacts/{a['artifact_id']}/audio",
                          "rel_path": a["rel_path"], "sha256": a["sha256"], "duration_ms": a["duration_ms"],
                          "sample_rate": a["sample_rate"], "format": a["format"], "file_present": a["file_present"]}
        else:
            d["audio"] = None
        return d

    def list(self, owner_id, narrator_id=None, limit=100):
        q, a = "SELECT generation_id FROM vl_generation_jobs WHERE owner_id=?", [owner_id]
        if narrator_id:
            q += " AND narrator_id=?"; a.append(narrator_id)
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute(q + " ORDER BY created_at DESC LIMIT ?", (*a, limit))]
        return [self.get(i, owner_id) for i in ids]

    def _set(self, gid, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.ctx.tx() as c:
            c.execute(f"UPDATE vl_generation_jobs SET {sets}, updated_at=? WHERE generation_id=?",
                      (*[canon(v) if isinstance(v, (dict, list)) else v for v in cols.values()], now(), gid))

    def run(self, gid, owner_id, should_abort):
        g = self.get(gid, owner_id)
        if g["status"] == "COMPLETED":
            return
        n, v = self.narr.resolve_version(g["narrator_id"], owner_id, g["model_version_id"])
        model = self.ctx.model(v["model_id"])
        backend = self.backends[model["runtime"]]
        self._set(gid, status="RUNNING", error=None, progress_note="synthesising")
        spoken, prep = prepare_text(g["text"], g["language"])
        chunks = chunk_text(spoken)
        pieces, metas, execution = [], [], None
        for i, ch in enumerate(chunks):
            if should_abort():
                raise RuntimeError("cancelled")
            self._set(gid, progress_note=f"synthesising chunk {i + 1}/{len(chunks)}")
            out = self.ctx.tmp_path(".wav")
            try:
                meta = backend.synthesize(model, v["config"], ch, out, int(g["config"]["seed"]) + i)
                if meta.get("execution"):
                    execution = meta["execution"]
                x = A.decode(out, OUT_SR)
            finally:
                out.unlink(missing_ok=True)  # Voice Lab's own per-job scratch file
            metas.append({k: meta.get(k) for k in ("gen_s", "audio_s", "rtf", "sample_rate", "rss_bytes")}
                         | {"load_s": (meta.get("load") or {}).get("load_s"), "chars": len(ch)})
            pieces.append(x)
            if i < len(chunks) - 1:
                pieces.append(np.zeros(int(OUT_SR * 0.25), np.float32))
        raw = np.concatenate(pieces).astype(np.float32)
        q_raw = A.quality(raw, OUT_SR)
        if q_raw["speech_ms"] < 200:
            raise RuntimeError("model produced no detectable speech")
        prov = {"narrator_id": n["narrator_id"], "narrator_name": n["display_name"],
                "model_version_id": v["model_version_id"], "model_version": v["version"], "model_kind": v["kind"],
                "identity_key": v["identity_key"], "model_id": v["model_id"],
                "weights_repo": model["weights"]["repo"], "weights_revision": model["weights"]["revision"],
                "code_commit": model["code"]["commit"], "commercial_status": model["commercial_status"],
                "production_allowed": bool(model.get("production_allowed")), "seed": g["config"]["seed"],
                "chunks": len(chunks), "runtime": metas, "generated_at": now(),
                "execution": execution or {"provider": "cpu", "gpu_workload_verified": False,
                                           "reason": "The backend did not report an execution device."}}
        if prep:
            prov["text_preparation"] = prep
        raw_art = self.ctx.register_audio(owner_id, raw, OUT_SR, kind="GENERATED_RAW",
                                          source={"generation_id": gid, "provenance": prov})
        features = None
        if g["style_profile_id"]:
            features = self.refs.transfer_features(owner_id, g["style_profile_id"])
            prov["style_profile_id"], prov["style_features_sha256"] = g["style_profile_id"], features["features_sha256"]
        self._set(gid, progress_note="applying delivery" if features else "measuring")
        styled, rep = S.apply_style(raw, OUT_SR, features, target_duration_ms=g["target_duration_ms"],
                                    narrator_cfg=v["config"], options=(g["config"] or {}).get("style_options"))
        q = A.quality(styled, OUT_SR)
        loud = A.loudness(styled, OUT_SR)
        art = self.ctx.register_audio(owner_id, styled, OUT_SR, kind="GENERATED",
                                      source={"generation_id": gid, "raw_artifact_id": raw_art["artifact_id"],
                                              "provenance": prov})
        timing = {"reference_duration_ms": rep.get("reference_duration_ms"),
                  "target_duration_ms": rep.get("target_duration_ms") or g["target_duration_ms"],
                  "raw_duration_ms": rep["raw_duration_ms"], "generated_duration_ms": art["duration_ms"],
                  "duration_diff_ms": (art["duration_ms"] - (rep.get("target_duration_ms") or g["target_duration_ms"]))
                  if (rep.get("target_duration_ms") or g["target_duration_ms"]) else None,
                  "style_report": rep, "quality": q, "loudness": loud}
        self._set(gid, status="COMPLETED", progress_note="completed", raw_artifact_id=raw_art["artifact_id"],
                  artifact_id=art["artifact_id"], timing_json=canon(timing), provenance_json=canon(prov))

    # ---------------------------------------------------------- Script Studio
    def script_result(self, g: dict) -> dict:
        ref = g.get("script_ref") or {}
        limitations = []
        prov = g.get("provenance") or {}
        if prov and not prov.get("production_allowed"):
            limitations.append(f"model {prov.get('model_id')} is {prov.get('commercial_status')}: not cleared for "
                               "monetised production")
        if prov.get("model_kind") == "BASE_SPEAKER":
            limitations.append("narrator uses a base-model speaker; not trained on the narrator's recordings")
        rep = (g.get("timing") or {}).get("style_report") or {}
        if rep.get("mode") == "GLOBAL_FALLBACK":
            limitations.append("phrase mapping not possible; global timing only: " + rep.get("reason", ""))
        if rep.get("stretch_capped_phrases") or rep.get("capped"):
            limitations.append("time-stretch cap reached; exact target duration not met")
        limitations += [f"pronunciation: {w}" for w in (prov.get("text_preparation") or {}).get("warnings", [])]
        return {"generation_id": g["generation_id"], "status": g["status"], "reused": g.get("reused", False),
                "script_id": ref.get("script_id"), "chapter_id": ref.get("chapter_id"),
                "paragraph_id": ref.get("paragraph_id"), "audio": g.get("audio"), "audio_url": (g.get("audio") or {}).get("url"),
                "duration_ms": (g.get("audio") or {}).get("duration_ms"),
                "format": (g.get("audio") or {}).get("format"),
                "narrator": {"narrator_id": g["narrator_id"], "model_version_id": g["model_version_id"],
                             "model_version": prov.get("model_version")},
                "style_profile_id": g["style_profile_id"], "config": g["config"], "timing": g.get("timing"),
                "provenance": prov or None, "error": g.get("error"), "limitations": limitations}
