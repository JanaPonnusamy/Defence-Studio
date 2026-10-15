"""Component-owned schema for the Voice Generation Engine."""
from __future__ import annotations
import json, sqlite3, time
from .direction import canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_licenses(
  license_id TEXT PRIMARY KEY, voice_id TEXT NOT NULL, speaker_license_ref TEXT NOT NULL,
  training_allowed INTEGER NOT NULL, commercial_use_allowed INTEGER NOT NULL, youtube_use_allowed INTEGER NOT NULL,
  valid_from TEXT, valid_until TEXT, terms_hash TEXT);
CREATE TABLE IF NOT EXISTS voice_models(
  voice_id TEXT PRIMARY KEY, adapter TEXT NOT NULL, display_name TEXT NOT NULL, languages_json TEXT NOT NULL,
  license_id TEXT NOT NULL REFERENCES voice_licenses, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS voice_model_versions(
  voice_id TEXT NOT NULL, model_version TEXT NOT NULL, capabilities_json TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY(voice_id, model_version));
CREATE TABLE IF NOT EXISTS voice_generations(
  generation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, voice_id TEXT NOT NULL, style_profile_id TEXT,
  language TEXT NOT NULL, status TEXT NOT NULL, error TEXT, audio_format TEXT, sample_rate INTEGER,
  duration_ms INTEGER, audio_reference TEXT, generation_version TEXT NOT NULL, generation_hash TEXT,
  direction_json TEXT NOT NULL, script TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, completed_at REAL);
CREATE INDEX IF NOT EXISTS ix_gen_req ON voice_generations(request_hash);
CREATE TABLE IF NOT EXISTS voice_generation_segments(
  generation_id TEXT NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL, start_ms INTEGER NOT NULL,
  end_ms INTEGER NOT NULL, controls_json TEXT NOT NULL, PRIMARY KEY(generation_id, idx));
CREATE TABLE IF NOT EXISTS voice_alignments(
  generation_id TEXT PRIMARY KEY, alignment_version TEXT NOT NULL, alignment_json TEXT NOT NULL, created_at REAL NOT NULL);
"""


class GenStore:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def put_voice(self, voice_id, adapter, display_name, languages, lic: dict, model_version, caps):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO voice_licenses VALUES(?,?,?,?,?,?,?,?,?)",
                            (lic["license_id"], voice_id, lic["speaker_license_ref"], int(lic["training_allowed"]),
                             int(lic["commercial_use_allowed"]), int(lic["youtube_use_allowed"]),
                             lic.get("valid_from"), lic.get("valid_until"), lic.get("terms_hash")))
            self.db.execute("INSERT OR REPLACE INTO voice_models VALUES(?,?,?,?,?,?)",
                            (voice_id, adapter, display_name, json.dumps(languages), lic["license_id"], time.time()))
            self.db.execute("INSERT OR IGNORE INTO voice_model_versions VALUES(?,?,?,?)",
                            (voice_id, model_version, canonical(caps), time.time()))

    def license_for(self, voice_id):
        r = self.db.execute("SELECT l.* FROM voice_licenses l JOIN voice_models m USING(license_id) WHERE m.voice_id=?",
                            (voice_id,)).fetchone()
        return dict(r) if r else None

    def find_request(self, request_hash):
        r = self.db.execute("SELECT * FROM voice_generations WHERE request_hash=? ORDER BY created_at DESC LIMIT 1",
                            (request_hash,)).fetchone()
        return dict(r) if r else None

    def generation(self, gid):
        r = self.db.execute("SELECT * FROM voice_generations WHERE generation_id=?", (gid,)).fetchone()
        return dict(r) if r else None

    def alignment(self, gid):
        r = self.db.execute("SELECT alignment_json FROM voice_alignments WHERE generation_id=?", (gid,)).fetchone()
        return json.loads(r[0]) if r else None

    def segments(self, gid):
        return [dict(r) for r in self.db.execute(
            "SELECT idx,text,start_ms,end_ms,controls_json FROM voice_generation_segments WHERE generation_id=? ORDER BY idx", (gid,))]
