"""Shared context: DB access, content-addressed artifact store, model catalogue, disk guard."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path

import numpy as np

from defence_studio.database import connect
from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab.common import (VoiceLabError, canon, loads, models_config_path, new_id, now,
                                             reserve_bytes, sha256_bytes, sha256_file)
from defence_studio.voice_lab.schema import ensure_schema

# Retention classes understood by Voice Lab. Every class here is PRESERVED; only
# TEMP_INTERMEDIATE may ever be offered for cleanup, and only via the Storage Manager.
RETENTION = {
    "SOURCE_RECORDING": "original dataset recording (never deleted by Voice Lab)",
    "DATASET_SEGMENT": "segment derived from an original recording",
    "REFERENCE_SOURCE": "authorised reference media as uploaded",
    "REFERENCE_SPEECH": "speech audio extracted from a reference",
    "ANALYSIS_FRAMES": "frame-level prosody measurements",
    "GENERATED_RAW": "model output before style processing",
    "GENERATED": "generated narration (styled or plain)",
    "PROJECT_UPLOAD": "audio imported into a mixer project",
    "EXPORT": "rendered project export",
    "MODEL_CHECKPOINT": "training checkpoint",
    "DATASET_MANIFEST": "immutable dataset version manifest",
    "BENCHMARK": "benchmark sample and report",
}


class Ctx:
    def __init__(self, database_file: Path, storage_root: Path):
        self.db_path = Path(database_file)
        self.storage_root = Path(storage_root)
        self.root = self.storage_root / "voice_lab"
        self.tmp = self.root / "tmp"
        for d in (self.root / "artifacts", self.tmp):
            d.mkdir(parents=True, exist_ok=True)
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self._catalogue = None

    # ---------------------------------------------------------------- database
    def ensure(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                with closing(connect(self.db_path)) as c:
                    ensure_schema(c)
                    self._seed_licences(c)
                self._schema_ready = True

    def conn(self) -> sqlite3.Connection:
        self.ensure()
        return connect(self.db_path)

    @contextmanager
    def tx(self):
        c = self.conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            if c.in_transaction:
                c.rollback()
            raise
        finally:
            c.close()

    # --------------------------------------------------------------- catalogue
    def catalogue(self) -> dict[str, dict]:
        if self._catalogue is None:
            data = json.loads(models_config_path().read_text(encoding="utf-8"))
            self._catalogue = {m["model_id"]: m for m in data["models"]}
        return self._catalogue

    def model(self, model_id: str) -> dict:
        m = self.catalogue().get(model_id)
        if not m:
            raise VoiceLabError(404, f"unknown model_id {model_id}")
        return m

    def _seed_licences(self, c):
        data = json.loads(models_config_path().read_text(encoding="utf-8"))
        for m in data["models"]:
            rec = {k: m.get(k) for k in ("code", "weights", "training_data", "access_terms", "commercial_status",
                                         "commercial_blockers", "attribution", "production_allowed")}
            c.execute("INSERT INTO vl_model_licences(model_id, display_name, record_json, commercial_status, recorded_at) "
                      "VALUES(?,?,?,?,?) ON CONFLICT(model_id) DO UPDATE SET display_name=excluded.display_name, "
                      "record_json=excluded.record_json, commercial_status=excluded.commercial_status, "
                      "recorded_at=excluded.recorded_at",
                      (m["model_id"], m["display_name"], canon(rec), m["commercial_status"], data["recorded_at"]))
        c.commit()

    # --------------------------------------------------------------- disk guard
    def require_space(self, needed_bytes: int) -> None:
        free = shutil.disk_usage(self.root).free
        if free - needed_bytes < reserve_bytes():
            raise VoiceLabError(507, {"code": "INSUFFICIENT_DISK", "free_bytes": free, "needed_bytes": needed_bytes,
                                      "reserve_bytes": reserve_bytes()})

    # ---------------------------------------------------------------- artifacts
    def tmp_path(self, suffix: str) -> Path:
        return self.tmp / f"{new_id('tmp')}{suffix}"

    def _place(self, sha: str, ext: str) -> tuple[Path, str]:
        rel = f"voice_lab/artifacts/{sha[:2]}/{sha}{ext}"
        return self.storage_root / rel, rel

    def register_bytes(self, owner_id: str, data: bytes, *, kind: str, fmt: str, source: dict,
                       sample_rate=None, channels=None, duration_ms=None, ext: str | None = None, conn=None) -> dict:
        if kind not in RETENTION:
            raise ValueError(f"unknown artifact kind {kind}")
        self.require_space(len(data))
        sha = sha256_bytes(data)
        path, rel = self._place(sha, ext or f".{fmt}")
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return self._insert(owner_id, kind, rel, sha, len(data), duration_ms, sample_rate, channels, fmt, source, conn)

    def register_file(self, owner_id: str, src: Path, *, kind: str, fmt: str, source: dict, move: bool = False,
                      sample_rate=None, channels=None, duration_ms=None) -> dict:
        size = src.stat().st_size
        self.require_space(0 if move else size)
        sha = sha256_file(src)
        path, rel = self._place(sha, src.suffix.lower() or f".{fmt}")
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            if move:
                os.replace(src, path)
            else:
                shutil.copyfile(src, path)
        elif move:
            src.unlink(missing_ok=True)  # identical content already stored; the temp copy is redundant
        return self._insert(owner_id, kind, rel, sha, size, duration_ms, sample_rate, channels, fmt, source)

    def register_audio(self, owner_id: str, x: np.ndarray, sr: int, *, kind: str, source: dict, conn=None) -> dict:
        data = A.wav_bytes(x, sr)
        ch = 1 if np.asarray(x).ndim == 1 else np.asarray(x).shape[1]
        return self.register_bytes(owner_id, data, kind=kind, fmt="wav", source=source, sample_rate=sr, channels=ch,
                                   duration_ms=int(len(x) * 1000 / sr), conn=conn)

    def _insert(self, owner_id, kind, rel, sha, size, duration_ms, sr, ch, fmt, source, conn=None):
        """Insert the artifact row; with ``conn`` it joins the caller's open transaction."""
        aid = new_id("art")

        def ins(c):
            c.execute("INSERT INTO vl_audio_artifacts(artifact_id, owner_id, kind, rel_path, sha256, bytes, duration_ms, "
                      "sample_rate, channels, format, retention_class, approved, source_json, created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                      (aid, owner_id, kind, rel, sha, size, duration_ms, sr, ch, fmt, kind, canon(source), now()))
        if conn is not None:
            ins(conn)
            return self.artifact(aid, owner_id, conn=conn)
        with self.tx() as c:
            ins(c)
        return self.artifact(aid, owner_id)

    def artifact(self, artifact_id: str, owner_id: str, conn=None) -> dict:
        if conn is not None:
            r = conn.execute("SELECT * FROM vl_audio_artifacts WHERE artifact_id=? AND owner_id=?",
                             (artifact_id, owner_id)).fetchone()
            return self._shape(r)
        with closing(self.conn()) as c:
            r = c.execute("SELECT * FROM vl_audio_artifacts WHERE artifact_id=? AND owner_id=?",
                          (artifact_id, owner_id)).fetchone()
        return self._shape(r)

    def _shape(self, r) -> dict:
        if not r:
            raise VoiceLabError(404, "artifact not found")
        d = dict(r)
        d["source"] = loads(d.pop("source_json"), {})
        d["approved"] = bool(d["approved"])
        d["file_present"] = (self.storage_root / d["rel_path"]).is_file()
        return d

    def artifact_path(self, artifact_id: str, owner_id: str, *, verify: bool = False) -> Path:
        a = self.artifact(artifact_id, owner_id)
        p = self.storage_root / a["rel_path"]
        if not p.is_file():
            raise VoiceLabError(410, {"code": "ARTIFACT_FILE_MISSING", "artifact_id": artifact_id, "rel_path": a["rel_path"]})
        if verify and sha256_file(p) != a["sha256"]:
            raise VoiceLabError(409, {"code": "ARTIFACT_HASH_MISMATCH", "artifact_id": artifact_id})
        return p
