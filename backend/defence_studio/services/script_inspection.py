"""Sanitised request/response inspection records for Phase 3 script generation.

One record is written per generation attempt that got past the evidence gate
(including attempts refused by the budget guard, which make zero provider
calls). A record keeps the *actual* client input and, when the provider
exposes it, the exact serialized wire body and raw response. It never holds
credentials: API keys / bearer tokens are scrubbed, local filesystem paths are
replaced, and the Authorization header is never captured at the source.

An invalid response is preserved verbatim for diagnosis and labelled with its
real validation outcome; nothing here promotes a failed response to a script.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from defence_studio.contracts.evidence import canonical
from defence_studio.providers.openai_text import scrub_secrets

SCHEMA_VERSION = "defence-studio.script-inspection.v1"
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'<>|]+|(?<![\w.])/(?:home|Users|root|var|tmp|mnt)/[^\s\"'<>|]+")
_ACCEPTED = {"AWAITING_REVIEW", "APPROVED"}


def sanitize(value, *secrets_: str):
    """Recursively scrub secrets and local filesystem paths from a JSON value."""
    if isinstance(value, str):
        return _PATH_RE.sub("<local-path>", scrub_secrets(value, *secrets_))
    if isinstance(value, list):
        return [sanitize(v, *secrets_) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v, *secrets_) for k, v in value.items()}
    return value


def _classify_request(system_text: str, user_text: str, claims: list[dict], wire_body: dict | None) -> dict:
    marker = "Approved evidence (the ONLY facts you may cite"
    head, _, tail = user_text.partition(marker)
    return {
        "instructions": {"system": system_text, "task": head.rstrip()},
        "normalized_source_material": [
            {"claim_id": c["claim_id"], "source_id": c.get("source_id", ""), "text": c.get("claim_text", ""),
             "passage": (c.get("passage") or {}).get("text") if isinstance(c.get("passage"), dict) else None}
            for c in claims],
        "evidence_references": {
            "claim_ids": [c["claim_id"] for c in claims],
            "source_ids": sorted({c.get("source_id", "") for c in claims if c.get("source_id")}),
        },
        "output_schema_hint": tail.split("Return a single JSON object", 1)[-1].strip() if marker in user_text else None,
        "wire_messages_match_client_input": (
            None if wire_body is None else
            wire_body.get("messages") == [{"role": "system", "content": system_text},
                                          {"role": "user", "content": user_text}]),
    }


def _clean_output(parsed: dict | None, claims_by_id: dict, accepted: bool) -> dict | None:
    if not isinstance(parsed, dict):
        return None
    groups = {"verified_facts": [], "attributed_claims": [], "uncertain_or_analysis": [], "other": []}
    key = {"VERIFIED_FACT": "verified_facts", "ATTRIBUTED_CLAIM": "attributed_claims",
           "ANALYSIS": "uncertain_or_analysis", "UNRESOLVED": "uncertain_or_analysis"}
    for section in parsed.get("sections") or []:
        if not isinstance(section, dict):
            continue
        ev = section.get("evidence") if isinstance(section.get("evidence"), dict) else {}
        refs = []
        for cid in ev.get("claim_ids") or []:
            c = claims_by_id.get(cid)
            refs.append({"claim_id": cid, "resolved": c is not None,
                         "claim_text": (c or {}).get("claim_text"), "source_id": (c or {}).get("source_id"),
                         "passage": ((c or {}).get("passage") or {}).get("text") if c else None})
        groups[key.get(section.get("statement_kind"), "other")].append({
            "section_id": section.get("section_id"), "heading": section.get("heading"),
            "narration": section.get("narration"), "statement_kind": section.get("statement_kind"),
            "qualification": ev.get("qualification"), "evidence": refs})
    return {
        "status_label": "ACCEPTED_FOR_REVIEW" if accepted else "REJECTED_NOT_A_SCRIPT",
        "title": parsed.get("title"), "opening_hook": parsed.get("opening_hook"),
        "closing": parsed.get("closing"), **groups,
    }


def _parsed_view(text: str | None) -> tuple[dict | None, dict]:
    """Return (parsed_object_or_None, parse_report). Never invents fields."""
    if text is None:
        return None, {"parsed": False, "error": "no response text"}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, {"parsed": False, "error": f"malformed JSON: {exc}"}
    if not isinstance(obj, dict):
        return None, {"parsed": False, "error": "top-level value is not a JSON object"}
    expected = {"title", "opening_hook", "sections", "closing"}
    section_keys = {"section_id", "heading", "narration", "requires_evidence", "statement_kind",
                    "evidence", "presentation_note"}
    problems = [f"missing top-level field {k!r}" for k in sorted(expected - obj.keys()) if k != "opening_hook"]
    problems += [f"unexpected top-level field {k!r}" for k in sorted(obj.keys() - expected)]
    if not isinstance(obj.get("sections"), list):
        problems.append("'sections' is not a list")
    else:
        for i, s in enumerate(obj["sections"]):
            if not isinstance(s, dict):
                problems.append(f"sections[{i}] is not an object")
                continue
            problems += [f"sections[{i}] missing {k!r}" for k in ("section_id", "narration") if k not in s]
            problems += [f"sections[{i}] unexpected field {k!r}" for k in sorted(s.keys() - section_keys)]
            if "narration" in s and not isinstance(s["narration"], str):
                problems.append(f"sections[{i}].narration has type {type(s['narration']).__name__}, expected string")
    return obj, {"parsed": True, "field_errors": problems}


def build_record(*, job_id: str, language: str, request_sha256: str, package_sha256: str,
                 outcome: str, detail: str | None, model: str, provider_name: str, max_output_tokens: int | None,
                 system_text: str, user_text: str, claims: list[dict], budget: dict,
                 exchange: dict | None, result=None, validation: dict | None = None,
                 schema_errors: list[str] | None = None, revision_version: int | None = None,
                 provider_calls: int = 0, api_key: str = "") -> dict:
    claims_by_id = {c["claim_id"]: c for c in claims}
    text = getattr(result, "text", None)
    if text is None and exchange and exchange.get("response"):
        # Failed call: keep whatever the provider body was, for diagnosis only.
        text = None
    parsed, parse_report = _parsed_view(text)
    if parsed is not None:
        known = set(claims_by_id)
        refs = []
        for s in parsed.get("sections") or []:
            if isinstance(s, dict):
                ev = s.get("evidence") if isinstance(s.get("evidence"), dict) else {}
                for cid in ev.get("claim_ids") or []:
                    refs.append({"section_id": s.get("section_id"), "claim_id": cid, "in_eligible_set": cid in known})
        parse_report["evidence_reference_checks"] = refs
    accepted = outcome in _ACCEPTED
    wire = (exchange or {}).get("request_body")
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:6]}",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "job_id": job_id, "language": language, "request_sha256": request_sha256, "package_sha256": package_sha256,
        "outcome": outcome, "outcome_detail": detail, "script_accepted": accepted, "revision_version": revision_version,
        "request": {
            "provider": provider_name, "model": model,
            "endpoint": (exchange or {}).get("endpoint"),
            "max_output_tokens": max_output_tokens,
            "client_input": {"model": model, "system_prompt": system_text, "user_prompt": user_text},
            "wire_body": wire,
            "wire_body_bytes": len(json.dumps(wire, ensure_ascii=False).encode("utf-8")) if wire else None,
            "structure": _classify_request(system_text, user_text, claims, wire),
            "budget_policy": budget,
            "timeout_s": (exchange or {}).get("timeout_s"), "max_retries": (exchange or {}).get("max_retries"),
            "output_constraint": (exchange or {}).get("output_constraint"),
        },
        "provider_calls": provider_calls,
        "raw_response": {
            "available": bool(exchange and exchange.get("response")) or text is not None,
            "http": (exchange or {}).get("response"),
            "attempts": (exchange or {}).get("attempts", []),
            "returned_text": text,
            "usage": None if result is None else {
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "tokens_available": result.tokens_available, "retries": result.retries,
                "measured_text_cost_inr": result.measured_text_cost_inr,
                "cost_is_measured": result.cost_is_measured, "pricing_source": result.pricing_source,
                "currency": result.currency},
        },
        "parsed_response": {"object": parsed, **parse_report, "schema_errors": schema_errors or []},
        "clean_output": _clean_output(parsed, claims_by_id, accepted),
        "validation_report": validation,
    }
    if outcome == "APPROVED" and record.get("clean_output"):
        record["clean_output"]["status_label"] = "AUTO_APPROVED"
    return sanitize(record, api_key)


def write(store, record: dict) -> str:
    job_id, language = record["job_id"], record["language"]
    uri = f"jobs/{job_id}/scripts/{language}/inspection/{record['record_id']}.json"
    store.write_bytes(job_id, uri, canonical(record), "application/json")
    return record["record_id"]


def list_records(store, job_id: str, language: str) -> list[dict]:
    directory = store.job_dir(job_id) / "scripts" / language / "inspection"
    out = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                r = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({k: r.get(k) for k in ("record_id", "recorded_at", "outcome", "script_accepted",
                                              "provider_calls", "revision_version", "request_sha256")})
    return out


def read(store, job_id: str, language: str, record_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9TZ]{16,24}-[0-9a-f]{6}", record_id):
        return None
    uri = f"jobs/{job_id}/scripts/{language}/inspection/{record_id}.json"
    if not store.exists(job_id, uri):
        return None
    return json.loads(store.read_text(job_id, uri))
