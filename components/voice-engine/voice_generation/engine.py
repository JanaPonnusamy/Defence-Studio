"""Voice Generation Engine: approved script + Voice Direction -> narration audio + measured alignment."""
from __future__ import annotations
import json, os, re, time, uuid
from datetime import date
import numpy as np
from . import wavutil
from .direction import DirectionError, MAX_SCRIPT_CHARS, canonical, sha, validate_direction
from .models import ModelError, VoiceModel
from .store import GenStore

GENERATION_VERSION = "1.0"
ALIGNMENT_VERSION = "1.0"
DEFAULT_SENTENCE_PAUSE_MS = 300


class GenerationError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def split_sentences(script: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।…])\s+|\n+", script.strip())
    return [p.strip() for p in parts if p.strip()]


class VoiceGenerationEngine:
    def __init__(self, store: GenStore | None = None, storage_dir: str = "./voice_storage"):
        self.store = store or GenStore()
        self.storage_dir = storage_dir
        self._models: dict[str, VoiceModel] = {}
        os.makedirs(storage_dir, exist_ok=True)

    # ---- voice registry -------------------------------------------------
    def register_voice(self, voice_id: str, model: VoiceModel, *, display_name: str, license: dict):
        need = {"license_id", "speaker_license_ref", "training_allowed", "commercial_use_allowed", "youtube_use_allowed"}
        if need - set(license):
            raise ValueError(f"license missing fields: {sorted(need - set(license))}")
        caps = model.capabilities()
        self.store.put_voice(voice_id, model.adapter, display_name, caps["languages"], license, model.version, caps)
        self._models[voice_id] = model

    def capabilities(self, voice_id):
        return self._models[voice_id].capabilities()

    # ---- generation -----------------------------------------------------
    def generate(self, script: str, direction: dict, *, require_commercial: bool = False,
                 should_abort=None, progress=None) -> dict:
        if not isinstance(script, str) or not script.strip():
            raise GenerationError("EMPTY_SCRIPT", "script is empty")
        if len(script) > MAX_SCRIPT_CHARS:
            raise GenerationError("SCRIPT_TOO_LONG", f"script exceeds {MAX_SCRIPT_CHARS} chars")
        d = validate_direction(direction)
        model = self._models.get(d["voice_id"])
        if model is None:
            raise GenerationError("UNKNOWN_VOICE", f"voice_id not registered: {d['voice_id']}")
        try:
            model.validate(d["language"])
        except ModelError as e:
            raise GenerationError("UNSUPPORTED_LANGUAGE", str(e))
        lic = self.store.license_for(d["voice_id"])
        today = date.today().isoformat()
        if not lic or (lic["valid_from"] and today < lic["valid_from"]) or (lic["valid_until"] and today > lic["valid_until"]):
            raise GenerationError("LICENSE_INVALID", "voice license missing or outside validity window")
        if require_commercial and not lic["commercial_use_allowed"]:
            raise GenerationError("LICENSE_INVALID", "voice license does not permit commercial use")

        req_hash = sha(canonical({"script": script.strip(), "direction": d, "model": model.adapter + model.version,
                                  "gv": GENERATION_VERSION}))
        prev = self.store.find_request(req_hash)
        if prev and prev["status"] == "COMPLETED":
            return self.describe(prev["generation_id"])          # idempotent replay
        gid = prev["generation_id"] if prev else "GEN-" + uuid.uuid4().hex[:16]
        attempt = (prev["attempt"] + 1) if prev else 1
        with self.store.db:
            if prev:
                self.store.db.execute("UPDATE voice_generations SET status='RUNNING', error=NULL, attempt=? WHERE generation_id=?",
                                      (attempt, gid))
                self.store.db.execute("DELETE FROM voice_generation_segments WHERE generation_id=?", (gid,))
            else:
                self.store.db.execute(
                    "INSERT INTO voice_generations(generation_id,request_hash,voice_id,style_profile_id,language,status,"
                    "generation_version,direction_json,script,attempt,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (gid, req_hash, d["voice_id"], d["style_profile_id"], d["language"], "RUNNING", GENERATION_VERSION,
                     canonical(d), script.strip(), 1, time.time()))
        try:
            if progress:
                progress("synthesizing")
            pcm, segs = self._render(model, script, d)
            if should_abort and should_abort():
                raise ModelError("generation aborted (job cancelled); result not published")
        except Exception as e:   # model failure is recorded, not swallowed
            with self.store.db:
                self.store.db.execute("UPDATE voice_generations SET status='FAILED', error=? WHERE generation_id=?",
                                      (f"{type(e).__name__}: {e}"[:500], gid))
            raise GenerationError("GENERATION_FAILED", str(e))
        wav = wavutil.to_wav_bytes(pcm, wavutil.OUT_SR)
        path = os.path.join(self.storage_dir, gid + ".wav")
        with open(path, "wb") as f:
            f.write(wav)
        dur = int(len(pcm) / wavutil.OUT_SR * 1000)
        ghash = sha(wav + canonical(d).encode() + GENERATION_VERSION.encode())
        words = self._word_timings(segs)
        alignment = {"alignment_version": ALIGNMENT_VERSION, "generation_id": gid, "aligner": {"name": "render-measured+proportional", "version": "1.0",
                                                          "forced_alignment_run": False},
                     "method": "RENDER_MEASURED_SENTENCES+PROPORTIONAL_WORDS",
                     "note": "sentence times measured from rendered audio; word times are proportional ESTIMATES "
                             "(no forced-aligner run)",
                     "segments": [{"text": s["text"], "start_ms": s["start_ms"], "end_ms": s["end_ms"],
                                   "measured": True, "accuracy": "MEASURED_RENDER_BOUNDARY"} for s in segs],
                     "words": words}
        if progress:
            progress("persisting")
        with self.store.db:
            for i, s in enumerate(segs):
                self.store.db.execute("INSERT INTO voice_generation_segments VALUES(?,?,?,?,?,?)",
                                      (gid, i, s["text"], s["start_ms"], s["end_ms"], canonical(s["controls"])))
            self.store.db.execute("INSERT OR REPLACE INTO voice_alignments VALUES(?,?,?,?)",
                                  (gid, ALIGNMENT_VERSION, canonical(alignment), time.time()))
            self.store.db.execute(
                "UPDATE voice_generations SET status='COMPLETED', audio_format='wav', sample_rate=?, duration_ms=?,"
                "audio_reference=?, generation_hash=?, completed_at=? WHERE generation_id=?",
                (wavutil.OUT_SR, dur, path, ghash, time.time(), gid))
        return self.describe(gid)

    def _render(self, model: VoiceModel, script: str, d: dict):
        sr = wavutil.OUT_SR
        pauses = {}
        for p in d["pauses"]:
            pauses.setdefault(p["position"], p["duration_ms"])
        emph = d["emphasis"]
        chunks, segs, cursor = [], [], 0

        def add_silence(ms):
            nonlocal cursor
            if ms > 0:
                chunks.append(np.zeros(int(sr * ms / 1000), np.float32))
                cursor += int(sr * ms / 1000)

        add_silence(pauses.get("before_speech", 0))
        sentences = split_sentences(script)
        for si, sent in enumerate(sentences):
            hit = [e for e in emph if e["text"].lower() in sent.lower()]
            strength = max((e["strength"] for e in hit), default=0.0)
            controls = {"tempo": d["tempo"] * (1 - 0.1 * strength), "energy": d["energy"],
                        "pitch_variation": d["pitch_variation"], "emphasis": strength}
            if hit:
                add_silence(pauses.get("before_emphasis", 0))
            x, msr = model.synthesize(sent, d["language"], controls)
            x = wavutil.resample(x, msr, sr)
            a, b = wavutil.speech_span_ms(x, sr)
            chunks.append(x)
            segs.append({"text": sent, "start_ms": int((cursor / sr) * 1000) + a,
                         "end_ms": int((cursor / sr) * 1000) + b, "controls": controls})
            cursor += len(x)
            if hit:
                add_silence(pauses.get("after_emphasis", 0))
            if si < len(sentences) - 1:
                add_silence(pauses.get("after_sentence", DEFAULT_SENTENCE_PAUSE_MS))
        out = np.concatenate(chunks) if chunks else np.zeros(1, np.float32)
        peak = float(np.abs(out).max())
        if peak < 1e-4:
            raise ModelError("model produced silent audio")
        return out, segs

    @staticmethod
    def _word_timings(segs):
        words = []
        for s in segs:
            toks = s["text"].split()
            total = sum(len(t) for t in toks) or 1
            t0, span = s["start_ms"], s["end_ms"] - s["start_ms"]
            acc = 0
            for t in toks:
                a = t0 + int(span * acc / total)
                acc += len(t)
                words.append({"text": t, "start_ms": a, "end_ms": t0 + int(span * acc / total), "measured": False,
                              "accuracy": "ESTIMATED_PROPORTIONAL"})
        return words

    # ---- reads ----------------------------------------------------------
    def describe(self, gid: str) -> dict | None:
        g = self.store.generation(gid)
        if not g:
            return None
        return {"generation_id": gid, "voice_id": g["voice_id"], "style_profile_id": g["style_profile_id"],
                "language": g["language"], "audio_format": g["audio_format"], "sample_rate": g["sample_rate"],
                "duration_ms": g["duration_ms"], "audio_reference": g["audio_reference"],
                "generation_version": g["generation_version"], "generation_hash": g["generation_hash"],
                "status": g["status"], "attempt": g["attempt"], "error": g["error"],
                "segments": [{"text": s["text"], "start_ms": s["start_ms"], "end_ms": s["end_ms"]}
                             for s in self.store.segments(gid)]}

    def alignment(self, gid: str):
        return self.store.alignment(gid)
