"""Persistence for audit snapshots (transaction-safe, append-only; see db.py)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import CALCULATION_VERSION, EXTRACTION_VERSION
from .db import TERMINAL_STATUSES, connect, migrate
from .masking import redact
from .parsing import FIELD_MAP, ParsedRecord

STATUSES = ("READY", "WAITING_FOR_LOGIN", "AUTHENTICATED", "INSPECTING", "EXTRACTING", "RECONCILING",
            "GENERATING_REPORTS", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED")

_TABLE = {"contribution": "esic_contribution_records", "interest": "esic_interest_records",
          "damages": "esic_damages_records", "recovery": "esic_recovery_records"}
# record field -> DB column where they differ
_COLUMN = {"contribution": {"wage_month": "contribution_month"}}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not serialisable: {type(value)}")


def dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False, sort_keys=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Repository:
    def __init__(self, db_path: Path):
        self.conn = connect(db_path)
        migrate(self.conn)
        self._lock = threading.RLock()

    @contextmanager
    def tx(self):
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def _one(self, sql: str, args=()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---- runs -------------------------------------------------------------
    def create_run(self, portal_url: str, synthetic: bool, rules_version: str) -> dict:
        audit_uuid = str(uuid.uuid4())
        ts = now_iso()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO esic_audit_runs (audit_uuid, started_at, status, current_activity, portal_url, synthetic, "
                "extraction_version, rules_version, calculation_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (audit_uuid, ts, "READY", "Audit created", portal_url, int(synthetic), EXTRACTION_VERSION,
                 rules_version, CALCULATION_VERSION, ts))
            c.execute("INSERT INTO esic_audit_stages (audit_id, stage, outcome, detail, recorded_at) VALUES (?,?,?,?,?)",
                      (cur.lastrowid, "READY", "DONE", "Audit created", ts))
        return self.get_run(audit_uuid)

    def get_run(self, audit_uuid: str) -> dict | None:
        row = self._one("SELECT * FROM esic_audit_runs WHERE audit_uuid=?", (audit_uuid,))
        return dict(row) if row else None

    def run_by_id(self, audit_id: int) -> dict:
        return dict(self._one("SELECT * FROM esic_audit_runs WHERE id=?", (audit_id,)))

    def list_runs(self, limit: int = 200) -> list[dict]:
        return self._all("SELECT * FROM esic_audit_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))

    def set_status(self, audit_id: int, status: str, activity: str | None = None, *, error: str | None = None,
                   employer_masked: str | None = None, records_inspected: int | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        sets, args = ["status=?", "current_activity=?"], [status, redact(activity) if activity else activity]
        if error is not None:
            sets.append("error_summary=?"); args.append(redact(error))
        if employer_masked is not None:
            sets.append("employer_identifier_masked=?"); args.append(employer_masked)
        if records_inspected is not None:
            sets.append("records_inspected=?"); args.append(records_inspected)
        if status in TERMINAL_STATUSES:
            sets.append("completed_at=?"); args.append(now_iso())
        with self.tx() as c:
            c.execute(f"UPDATE esic_audit_runs SET {', '.join(sets)} WHERE id=?", (*args, audit_id))
            c.execute("INSERT INTO esic_audit_stages (audit_id, stage, outcome, detail, recorded_at) VALUES (?,?,?,?,?)",
                      (audit_id, status, "ENTERED", redact(activity or ""), now_iso()))

    def update_activity(self, audit_id: int, activity: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE esic_audit_runs SET current_activity=? WHERE id=?", (redact(activity), audit_id))

    def add_stage(self, audit_id: int, stage: str, outcome: str, detail: str = "") -> None:
        with self.tx() as c:
            c.execute("INSERT INTO esic_audit_stages (audit_id, stage, outcome, detail, recorded_at) VALUES (?,?,?,?,?)",
                      (audit_id, stage, outcome, redact(detail), now_iso()))

    def add_event(self, audit_id: int, level: str, message: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO esic_audit_events (audit_id, level, message, recorded_at) VALUES (?,?,?,?)",
                      (audit_id, level, redact(message), now_iso()))

    # ---- captures / evidence ----------------------------------------------
    def add_capture(self, audit_id: int, category: str, page_name: str, page_url: str | None, tables: list[dict]) -> int | None:
        payload = dumps(tables)
        digest = sha256_bytes(f"{category}|{payload}".encode())
        with self.tx() as c:
            if c.execute("SELECT 1 FROM esic_raw_captures WHERE audit_id=? AND content_hash=?", (audit_id, digest)).fetchone():
                return None
            cur = c.execute("INSERT INTO esic_raw_captures (audit_id, category, page_name, page_url, captured_at, tables_json, content_hash) "
                            "VALUES (?,?,?,?,?,?,?)", (audit_id, category, page_name, page_url, now_iso(), payload, digest))
            return cur.lastrowid

    def captures(self, audit_id: int) -> list[dict]:
        rows = self._all("SELECT * FROM esic_raw_captures WHERE audit_id=? ORDER BY id", (audit_id,))
        for r in rows:
            r["tables"] = json.loads(r.pop("tables_json"))
        return rows

    def add_evidence(self, audit_id: int, evidence_type: str, page_name: str, path: Path, data: bytes, status: str) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        row = {"audit_id": audit_id, "evidence_type": evidence_type, "page_name": page_name, "captured_at": now_iso(),
               "artifact_path": str(path), "content_hash": sha256_bytes(data), "extraction_status": status}
        with self.tx() as c:
            c.execute("INSERT INTO esic_audit_evidence (audit_id, evidence_type, page_name, captured_at, artifact_path, content_hash, "
                      "extraction_status) VALUES (:audit_id,:evidence_type,:page_name,:captured_at,:artifact_path,:content_hash,:extraction_status)", row)
        return row

    def evidence(self, audit_id: int) -> list[dict]:
        return self._all("SELECT * FROM esic_audit_evidence WHERE audit_id=? ORDER BY id", (audit_id,))

    # ---- records ------------------------------------------------------------
    def store_records(self, audit_id: int, records: list[ParsedRecord]) -> tuple[int, int]:
        """Insert parsed records in ONE transaction. Returns (inserted, duplicates_ignored)."""
        inserted = dupes = 0
        ts = now_iso()
        with self.tx() as c:
            for rec in records:
                table = _TABLE[rec.category]
                if c.execute(f"SELECT 1 FROM {table} WHERE audit_id=? AND record_key=?", (audit_id, rec.record_key)).fetchone():
                    dupes += 1
                    continue
                cols = {"audit_id": audit_id, "record_key": rec.record_key, "source_reference": rec.source_reference,
                        "raw_json": dumps(rec.raw), "extracted_at": ts}
                for fld, value in rec.values.items():
                    if fld == "challan_created_date" and rec.category != "contribution":
                        continue
                    col = _COLUMN.get(rec.category, {}).get(fld, fld)
                    cols[col] = _json_default(value) if isinstance(value, (Decimal, date)) else value
                names = ",".join(cols)
                c.execute(f"INSERT INTO {table} ({names}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values()))
                inserted += 1
        return inserted, dupes

    def load_records(self, audit_id: int) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for cat, table in _TABLE.items():
            kinds = {fld: kind for fld, kind, _ in FIELD_MAP[cat]}
            reverse = {v: k for k, v in _COLUMN.get(cat, {}).items()}
            rows = []
            for row in self._all(f"SELECT * FROM {table} WHERE audit_id=? ORDER BY id", (audit_id,)):
                rec = {"source_reference": row["source_reference"], "raw": json.loads(row["raw_json"])}
                for col, value in row.items():
                    fld = reverse.get(col, col)
                    if fld not in kinds:
                        continue
                    if value is None:
                        rec[fld] = None
                    elif kinds[fld] == "money":
                        rec[fld] = Decimal(value)
                    elif kinds[fld] == "date":
                        rec[fld] = date.fromisoformat(value)
                    else:
                        rec[fld] = value
                rows.append(rec)
            out[cat] = rows
        return out

    # ---- results / reports ---------------------------------------------------
    def store_result(self, audit_id: int, result: dict) -> str:
        payload = dumps(result)
        digest = sha256_bytes(payload.encode())
        with self.tx() as c:
            c.execute("INSERT INTO esic_audit_results (audit_id, result_json, result_hash, created_at) VALUES (?,?,?,?)",
                      (audit_id, payload, digest, now_iso()))
        return digest

    def load_result(self, audit_id: int) -> dict | None:
        row = self._one("SELECT result_json, result_hash FROM esic_audit_results WHERE audit_id=?", (audit_id,))
        if not row:
            return None
        result = json.loads(row["result_json"])
        result["_result_hash"] = row["result_hash"]
        return result

    def add_report(self, audit_id: int, report_type: str, path: Path) -> dict:
        row = {"audit_id": audit_id, "report_type": report_type, "file_path": str(path),
               "generated_at": now_iso(), "checksum": sha256_bytes(path.read_bytes())}
        with self.tx() as c:
            c.execute("INSERT INTO esic_audit_reports (audit_id, report_type, file_path, generated_at, checksum) "
                      "VALUES (:audit_id,:report_type,:file_path,:generated_at,:checksum)", row)
        return row

    def reports(self, audit_id: int) -> list[dict]:
        return self._all("SELECT * FROM esic_audit_reports WHERE audit_id=? ORDER BY id", (audit_id,))

    def stages(self, audit_id: int) -> list[dict]:
        return self._all("SELECT stage, outcome, detail, recorded_at FROM esic_audit_stages WHERE audit_id=? ORDER BY id", (audit_id,))

    def events(self, audit_id: int) -> list[dict]:
        return self._all("SELECT level, message, recorded_at FROM esic_audit_events WHERE audit_id=? ORDER BY id", (audit_id,))
