CREATE TABLE assets(asset_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE rights_revisions(entity_id TEXT NOT NULL REFERENCES assets(asset_id), revision INTEGER NOT NULL CHECK(revision>0), payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(entity_id,revision));
CREATE TABLE assignments(assignment_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(asset_id));
CREATE TABLE assignment_revisions(entity_id TEXT NOT NULL REFERENCES assignments(assignment_id), revision INTEGER NOT NULL CHECK(revision>0), payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(entity_id,revision));
CREATE TABLE review_state(kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL, decision TEXT NOT NULL, PRIMARY KEY(kind,entity_id));
CREATE TABLE review_history(sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL, reviewer TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL, content_digest TEXT NOT NULL, reviewed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, request_payload TEXT NOT NULL);
CREATE TABLE migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL);
