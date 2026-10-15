"""Component-owned SQLite schema. Profile versions + segments are immutable (triggers)."""
from __future__ import annotations
import json, sqlite3, time, uuid
from .analyzer import canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_sources(
  source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, location TEXT NOT NULL, metadata_json TEXT NOT NULL,
  provenance_state TEXT NOT NULL, authorization_ref TEXT NOT NULL, retain_audio INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS voice_profiles(
  voice_style_id TEXT PRIMARY KEY, profile_name TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES voice_sources,
  language TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS voice_profile_versions(
  voice_style_id TEXT NOT NULL REFERENCES voice_profiles, version INTEGER NOT NULL,
  analysis_version TEXT NOT NULL, analysis_hash TEXT NOT NULL, profile_json TEXT NOT NULL,
  note TEXT, created_at REAL NOT NULL, PRIMARY KEY(voice_style_id, version));
CREATE TABLE IF NOT EXISTS voice_style_segments(
  voice_style_id TEXT NOT NULL, version INTEGER NOT NULL, segment_id TEXT NOT NULL,
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, segment_json TEXT NOT NULL,
  PRIMARY KEY(voice_style_id, version, segment_id));
CREATE TRIGGER IF NOT EXISTS pv_no_update BEFORE UPDATE ON voice_profile_versions
  BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER IF NOT EXISTS pv_no_delete BEFORE DELETE ON voice_profile_versions
  BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER IF NOT EXISTS seg_no_update BEFORE UPDATE ON voice_style_segments
  BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER IF NOT EXISTS seg_no_delete BEFORE DELETE ON voice_style_segments
  BEGIN SELECT RAISE(ABORT,'immutable'); END;
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add_source(self, kind, location, metadata, provenance_state, authorization_ref, retain_audio=False):
        sid = "SRC-" + uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO voice_sources VALUES(?,?,?,?,?,?,?,?)",
                        (sid, kind, location, canonical(metadata), provenance_state, authorization_ref,
                         int(retain_audio), time.time()))
        self.db.commit()
        return sid

    def get_source(self, sid):
        r = self.db.execute("SELECT * FROM voice_sources WHERE source_id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def find_by_hash(self, source_id, ahash):
        r = self.db.execute(
            "SELECT v.voice_style_id, v.version FROM voice_profile_versions v JOIN voice_profiles p USING(voice_style_id)"
            " WHERE p.source_id=? AND v.analysis_hash=? ORDER BY v.version LIMIT 1", (source_id, ahash)).fetchone()
        return (r[0], r[1]) if r else None

    def save(self, voice_style_id, profile_name, source_id, language, profile, segments, ahash, note=None):
        """New profile (version 1) or, if voice_style_id exists, a NEW version. Never mutates."""
        row = self.db.execute("SELECT MAX(version) FROM voice_profile_versions WHERE voice_style_id=?",
                              (voice_style_id,)).fetchone()
        ver = (row[0] or 0) + 1
        with self.db:
            if ver == 1:
                self.db.execute("INSERT INTO voice_profiles VALUES(?,?,?,?,?)",
                                (voice_style_id, profile_name, source_id, language, time.time()))
            self.db.execute("INSERT INTO voice_profile_versions VALUES(?,?,?,?,?,?,?)",
                            (voice_style_id, ver, profile["analysis_version"], ahash, canonical(profile), note, time.time()))
            for s in segments:
                self.db.execute("INSERT INTO voice_style_segments VALUES(?,?,?,?,?,?)",
                                (voice_style_id, ver, s["segment_id"], s["start_ms"], s["end_ms"], canonical(s)))
        return ver

    def profile(self, voice_style_id, version=None):
        q, a = "SELECT * FROM voice_profile_versions WHERE voice_style_id=?", [voice_style_id]
        if version:
            q += " AND version=?"
            a.append(version)
        r = self.db.execute(q + " ORDER BY version DESC LIMIT 1", a).fetchone()
        if not r:
            return None
        p = self.db.execute("SELECT * FROM voice_profiles WHERE voice_style_id=?", (voice_style_id,)).fetchone()
        return {"voice_style_id": voice_style_id, "profile_name": p["profile_name"], "source_id": p["source_id"],
                "version": r["version"], "analysis_hash": r["analysis_hash"], **json.loads(r["profile_json"])}

    def segments(self, voice_style_id, version=None):
        p = self.profile(voice_style_id, version)
        if not p:
            return None
        rows = self.db.execute("SELECT segment_json FROM voice_style_segments WHERE voice_style_id=? AND version=?"
                               " ORDER BY start_ms", (voice_style_id, p["version"])).fetchall()
        return [{"style_id": voice_style_id, **json.loads(r[0])} for r in rows]
