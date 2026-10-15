"""Read-only check against the task-start snapshot; never opens the DB writable."""
import hashlib
import json
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parents[2]
evidence = Path(__file__).resolve().parent
before = json.loads((evidence / "preservation-before.json").read_text())
after = {"files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in before["files"]}}
db = root / "data/defence_studio.sqlite3"
with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
    after["schema"] = connection.execute(
        "select type,name,tbl_name,sql from sqlite_master order by type,name").fetchall()
    # Names come only from the allowlisted task-start review tables.
    tables = {"claim_reviews", "claim_review_history", "source_reviews", "source_review_history"}
    assert set(before["reviews"]) <= tables
    after["reviews"] = {name: connection.execute("select * from " + name + " order by rowid").fetchall()
                        for name in before["reviews"]}
after = json.loads(json.dumps(after, default=str))
side_names = {"defence_studio.sqlite3-wal", "defence_studio.sqlite3-shm"}
current_side_files = {str(p.relative_to(root)).replace("\\", "/")
                      for p in db.parent.iterdir() if p.name in side_names and p.is_file()}
original_side_files = {name.replace("\\", "/") for name in before["files"]
                       if Path(name).name in side_names}
result = {"database_side_files_unchanged": current_side_files == original_side_files,
          "files_unchanged": after["files"] == before["files"],
          "schema_unchanged": after["schema"] == before["schema"],
          "human_reviews_unchanged": after["reviews"] == before["reviews"]}
print(json.dumps(result, indent=2))
(evidence / "preservation-result.json").write_text(json.dumps(result, indent=2))
assert all(result.values()), "Preservation check failed; investigate without restoring or overwriting data"
