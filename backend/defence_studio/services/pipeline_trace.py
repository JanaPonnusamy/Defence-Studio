"""Read-only per-source trace of the research pipeline for inspection:
acquisition -> raw extracted content -> clean normalized text -> claims ->
supporting passages, with a defect-origin hint per source.

Nothing here rewrites or "cleans" source facts: it only reads stored,
hash-verified artifacts and reports what each stage kept or dropped.
"""
from __future__ import annotations

import hashlib
import re
from contextlib import closing
from html import unescape

from defence_studio.database import connect
from defence_studio.services.evidence import EvidenceService

_RAW_LIMIT = 200_000
_FACT_TOKEN = re.compile(r"\d[\d,.:/%-]*\d|\d")
_TAGS = re.compile(r"<(script|style)\b.*?</\1\s*>|<[^>]+>", re.S | re.I)


def _fact_tokens(text: str) -> set[str]:
    return set(_FACT_TOKEN.findall(text))


class PipelineTraceService(EvidenceService):
    def pipeline_trace(self, job_id: str, owner_id: str) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            acq_ref = self._latest_artifact(connection, job_id, "acquisition_manifest")
            ana_ref = self._latest_artifact(connection, job_id, "analysis_result")
        acquisition = self._read_json_artifact(job_id, acq_ref) if acq_ref else None
        analysis = self._read_json_artifact(job_id, ana_ref) if ana_ref else None
        if analysis is None:
            return {"job_id": job_id, "available": False, "reason": "No analysis_result artifact for this job",
                    "acquisition_sha256": acq_ref["sha256"] if acq_ref else None, "sources": []}

        acq_by_id = {s.get("source_id"): s for s in (acquisition or {}).get("sources", [])}
        claims_by_source: dict[str, list] = {}
        for c in analysis.get("claims", []):
            claims_by_source.setdefault(c.get("source_id"), []).append(c)
        dup_by_source: dict[str, list] = {}
        for g in analysis.get("duplicate_groups", []):
            for sid in g.get("member_source_ids", []) or []:
                dup_by_source.setdefault(sid, []).append(g)
        failures = {f.get("source_id"): f for f in analysis.get("failures", []) if isinstance(f, dict)}

        sources = []
        for ns in analysis.get("normalized_sources", []):
            sid = ns.get("source_id")
            acq = acq_by_id.get(sid, {})
            art = ns.get("original_artifact") or {}
            raw_text, raw_note, raw_ok = None, None, None
            if art.get("uri"):
                try:
                    data = self.store.read_bytes(job_id, art["uri"], max_bytes=64 * 1024 * 1024)
                    raw_ok = hashlib.sha256(data).hexdigest() == art.get("sha256")
                    raw_text = data[:_RAW_LIMIT].decode("utf-8", errors="replace")
                    if len(data) > _RAW_LIMIT:
                        raw_note = f"truncated for display: {len(data)} bytes total"
                except (OSError, ValueError) as exc:
                    raw_note = f"raw artifact unreadable: {exc}"
            clean = ns.get("normalized_text") or ""
            claims = claims_by_source.get(sid, [])
            passage_checks = []
            for c in claims:
                p = c.get("passage") or {}
                s, e = p.get("start_offset"), p.get("end_offset")
                ok = None
                if p.get("kind") == "TEXT_SPAN":
                    ok = isinstance(s, int) and isinstance(e, int) and clean[s:e] == p.get("text")
                passage_checks.append({"claim_id": c.get("claim_id"), "start": s, "end": e, "kind": p.get("kind"),
                                       "matches_normalized_text": ok})
            visible_raw = unescape(_TAGS.sub(" ", raw_text)) if raw_text and "<" in raw_text else (raw_text or "")
            lost = sorted(_fact_tokens(visible_raw) - _fact_tokens(clean))
            findings = []
            stage = None
            if acq and acq.get("status") != "ACQUIRED":
                stage = "acquisition"
                findings.append(f"acquisition status {acq.get('status')}: {acq.get('error')}")
            elif raw_ok is False:
                stage = "acquisition"
                findings.append("stored raw bytes fail their recorded SHA-256")
            elif ns.get("status") != "NORMALIZED" or sid in failures:
                stage = "extraction"
                findings.append(f"normalization status {ns.get('status')}: {failures.get(sid)}")
            elif not clean.strip():
                stage = "normalization"
                findings.append("normalized text is empty")
            elif lost:
                stage = "normalization"
                findings.append(f"{len(lost)} numeric token(s) in raw visible text are absent from "
                                "normalized text (may be boilerplate; inspect)")
            if stage is None and not claims:
                stage = "claim_extraction"
                findings.append("no claims extracted from non-empty normalized text")
            if any(c["matches_normalized_text"] is False for c in passage_checks):
                stage = stage or "claim_extraction"
                findings.append("a claim passage does not match normalized text at its offsets")
            if dup_by_source.get(sid):
                findings.append("source is a member of a duplicate group (deduplication stage)")
            sources.append({
                "source_id": sid, "status": ns.get("status"),
                "defect_origin": stage, "findings": findings,
                "acquisition": {
                    "status": acq.get("status"), "method": acq.get("method"), "requested_url": acq.get("requested_url"),
                    "canonical_url": acq.get("canonical_url"), "final_url": acq.get("final_url"),
                    "acquired_at": acq.get("acquisition_timestamp"), "warnings": acq.get("warnings", []),
                    "error": acq.get("error"), "artifact": art},
                "raw": {"text": raw_text, "chars_shown": len(raw_text or ""), "note": raw_note,
                        "sha256_verified": raw_ok, "media_type": art.get("media_type")},
                "clean": {"text": clean, "chars": ns.get("normalized_char_count"), "sha256": ns.get("normalized_sha256"),
                          "normalization_version": ns.get("normalization_version"),
                          "language": ns.get("language_detected"), "warnings": ns.get("warnings", []),
                          "numeric_tokens_dropped_vs_raw_visible_text": lost},
                "structured": {k: ns.get(k) for k in ("canonical_url", "dates", "declared_metadata", "media_type",
                                                      "source_type", "original_sha256_verified")},
                "duplicate_groups": dup_by_source.get(sid, []),
                "claims": [{"claim_id": c.get("claim_id"), "claim_text": c.get("claim_text"),
                            "entities": c.get("entities"), "qualifiers": c.get("qualifiers"),
                            "has_negation": c.get("has_negation"), "passage": c.get("passage"),
                            "extractor_version": c.get("extractor_version"), "warnings": c.get("warnings", [])}
                           for c in claims],
                "passage_checks": passage_checks,
            })
        return {
            "job_id": job_id, "available": True,
            "hashes": {"acquisition_manifest_sha256": acq_ref["sha256"] if acq_ref else None,
                       "analysis_result_sha256": ana_ref["sha256"],
                       "analysis_input_acquisition_sha256": analysis.get("input_acquisition_sha256"),
                       "analysis_input_digest": analysis.get("input_digest")},
            "versions": analysis.get("versions"), "claims_total": analysis.get("claims_total"),
            "claim_groups": analysis.get("claim_groups", []), "conflicts": analysis.get("conflicts", []),
            "analysis_failures": analysis.get("failures", []), "warnings": analysis.get("warnings", []),
            "sources": sources,
        }
