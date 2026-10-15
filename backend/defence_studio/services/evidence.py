"""Atomic, revision-bound evidence approval over existing F3/F4/F5 artifacts.

The server assembles all content itself; callers supply only the revision they
reviewed and an explanation. The approval ledger is separate from the generic
artifact-registration API, which cannot confer approval authority.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

from defence_studio.contracts.evidence import (
    PACKAGE_SCHEMA, POLICY, canonical, digest, timestamp, validate_package,
)
from defence_studio.database import connect
from defence_studio.repositories.jobs import JobRepository
from defence_studio.services.review import ReviewService, ReviewNotFound, ReviewValidationError, StaleRevision


class EvidenceService(ReviewService):
    def _snapshot(self, connection, job_id, owner_id):
        try:
            return self._assemble(connection, job_id, owner_id)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ReviewValidationError("Malformed evidence artifact or review record") from exc

    def _assemble(self, connection, job_id, owner_id):
        job = JobRepository(connection).get_owned_job(job_id, owner_id)
        if job is None:
            raise ReviewNotFound("Workflow job not found")
        blockers, limitations, refs, payloads = [], [], [], {}
        for kind in ("acquisition_manifest", "analysis_result", "discovery_result", "acquisition_request"):
            ref = self._latest_artifact(connection, job_id, kind)
            if ref is None:
                if kind in ("acquisition_manifest", "analysis_result"):
                    blockers.append(f"Missing {kind}")
                continue
            refs.append({k: ref[k] for k in ("artifact_type", "uri", "sha256", "schema_version")})
            payloads[kind] = self._read_json_artifact(job_id, ref)
            if payloads[kind].get("job_id") != job_id:
                raise ReviewValidationError("Artifact belongs to a different job")
            expected_schema = {
                "acquisition_manifest": "defence-studio.acquisition-result.v1",
                "analysis_result": "defence-studio.analysis-result.v1",
                "discovery_result": "defence-studio.discovery-result.v1",
                "acquisition_request": "defence-studio.acquisition-request.v1",
            }[kind]
            accepted = {expected_schema}
            if kind == "discovery_result":
                accepted.add("defence-studio.discovery-result.v2")  # additive v2; v1 stays readable
            if ref["schema_version"] not in accepted or payloads[kind].get("schema_version") not in accepted:
                raise ReviewValidationError(f"Unsupported {kind} schema")
        acq, analysis = payloads.get("acquisition_manifest", {}), payloads.get("analysis_result", {})
        by_type = {r["artifact_type"]: r for r in refs}
        if analysis and acq and (
            analysis.get("input_acquisition_sha256") != by_type["acquisition_manifest"]["sha256"] or
            analysis.get("input_acquisition_uri") != by_type["acquisition_manifest"]["uri"]
        ):
            blockers.append("Analysis does not reference the current acquisition revision")
        for stage in ("topic_search_and_source_collection", "extraction_validation_normalization_deduplication"):
            row = connection.execute("SELECT status FROM workflow_stages WHERE job_id=? AND stage_key=?",
                                     (job_id, stage)).fetchone()
            if not row or row[0] != "COMPLETED":
                blockers.append(f"Stage not completed: {stage}")
        reviews, audit = {}, {}
        for kind in ("source", "claim"):
            key = kind + "_id"
            reviews[kind] = {r[key]: dict(r) for r in connection.execute(
                f"SELECT * FROM {kind}_reviews WHERE job_id=? ORDER BY {key}", (job_id,))}
            audit[kind] = [dict(r) for r in connection.execute(
                f"SELECT * FROM {kind}_review_history WHERE job_id=? ORDER BY occurred_at, {key}, revision",
                (job_id,))]
        # Automated policy decisions live in their own append-only table and only count when they were
        # computed for EXACTLY the current acquisition + analysis artifacts. A human decision always wins.
        from defence_studio.repositories import policy_decisions as _pd
        automated = {"source": {}, "claim": {}}
        policy_rows = []
        if by_type.get("acquisition_manifest") and by_type.get("analysis_result"):
            policy_rows = _pd.latest_run(connection, job_id, by_type["acquisition_manifest"]["sha256"],
                                         by_type["analysis_result"]["sha256"])
        for row in policy_rows:
            kind = row["subject_type"].lower()
            ref_sha = by_type["acquisition_manifest" if kind == "source" else "analysis_result"]["sha256"]
            ok = row["state"] == "AUTO_ACCEPTED"
            automated[kind][row["subject_id"]] = {
                "origin": "AUTOMATED_POLICY", "decision": "APPROVED_FOR_USE" if ok else (
                    "REJECTED" if row["state"] in ("REJECTED", "SOURCE_BLOCKED") else "NEEDS_MORE_EVIDENCE"),
                "policy_state": row["state"], "policy_version": row["policy_version"], "run_id": row["run_id"],
                "score": row["score"], "claim_class": row["claim_class"],
                "independent_groups": row["independent_groups"],
                "reviewer_id": None, "note": "; ".join(row["reasons"])[:4000] or row["state"],
                "reviewed_artifact_sha256": ref_sha, "revision": 0}
        for kind in ("source", "claim"):
            for rec in reviews[kind].values():
                rec["origin"] = "HUMAN"
            for sid_, rec in automated[kind].items():
                reviews[kind].setdefault(sid_, rec)
        normalized = {n["source_id"]: n for n in analysis.get("normalized_sources", [])}
        if len(normalized) != len(analysis.get("normalized_sources", [])):
            raise ReviewValidationError("Duplicate normalized source identity")
        sources, claims, events = [], [], []

        def event(kind, original, source_id=None, claim_id=None, language=None, passage=None, url=None):
            events.append({"kind": kind, "date": timestamp(original), "source_id": source_id,
                           "claim_id": claim_id, "language": language, "passage": passage,
                           "original_url": url})

        def review_state(kind, subject, ref_kind):
            record = reviews[kind].get(subject)
            stale = bool(record) and record["reviewed_artifact_sha256"] != by_type.get(ref_kind, {}).get("sha256")
            # An AUTOMATED_POLICY disposition that leaves a claim unresolved is an explicit, explained
            # exclusion (not usable, not blocking). The same decision made by a PERSON still blocks.
            policy_exclusion = bool(record) and record.get("origin") == "AUTOMATED_POLICY" and \
                record["decision"] == "NEEDS_MORE_EVIDENCE"
            if not record or stale or not record["note"].strip() or (
                    record["decision"] not in {"APPROVED_FOR_USE", "REJECTED"} and not policy_exclusion):
                blockers.append(f"{kind} {subject}: current final review with explanation required")
            return record, stale

        source_ids = set()
        for source in acq.get("sources", []):
            sid = source["source_id"]
            if sid in source_ids:
                raise ReviewValidationError("Duplicate source identity")
            source_ids.add(sid)
            for ref in source.get("artifacts", []):
                try:
                    if self.store.sha256_of(job_id, ref["uri"]) != ref["sha256"]:
                        raise ValueError("checksum mismatch")
                except (OSError, ValueError, KeyError):
                    blockers.append(f"Source {sid}: artifact missing or corrupt")
            norm = normalized.get(sid)
            if norm and norm.get("status") == "NORMALIZED":
                text = norm["normalized_text"]
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != norm["normalized_sha256"]:
                    raise ReviewValidationError("Normalized text checksum mismatch")
                original = norm.get("original_artifact")
                if not original or not any(original.get("uri") == r.get("uri") and original.get("sha256") == r.get("sha256")
                                           for r in source.get("artifacts", [])):
                    blockers.append(f"Source {sid}: analysis byte reference is not in acquisition")
            record, stale = review_state("source", sid, "acquisition_manifest")
            if record and record["decision"] == "APPROVED_FOR_USE" and (
                source.get("status") != "ACQUIRED" or not source.get("artifacts") or
                not norm or norm.get("status") != "NORMALIZED"
            ):
                blockers.append(f"Source {sid}: approved source lacks usable acquired evidence")
            meta = source.get("declared_metadata", {})
            if not meta.get("published_at"):
                limitations.append(f"Source {sid}: publication date unavailable")
            if not meta.get("publisher"):
                limitations.append(f"Source {sid}: publisher unknown")
            if source.get("status") != "ACQUIRED" or not norm or norm.get("status") != "NORMALIZED":
                limitations.append(f"Source {sid}: acquisition or normalization incomplete")
            language = (norm or {}).get("language_detected") or meta.get("language")
            source_view = {**source, "normalized": ({k: v for k, v in norm.items() if k != "normalized_text"} if norm else None),
                           "review": record, "is_stale": stale}
            sources.append(source_view)
            for kind, date in (("PUBLICATION", meta.get("published_at")), ("ACQUISITION", source.get("acquisition_timestamp"))):
                event(kind, date, sid, language=language, url=source.get("requested_url"))
        eligible, claim_ids = [], set()
        sources_by_id = {s["source_id"]: s for s in sources}
        for claim in analysis.get("claims", []):
            cid, sid = claim["claim_id"], claim["source_id"]
            if cid in claim_ids:
                raise ReviewValidationError("Duplicate claim identity")
            claim_ids.add(cid)
            passage = claim.get("passage", {})
            norm, source = normalized.get(sid, {}), sources_by_id.get(sid, {})
            start, end = passage.get("start_offset"), passage.get("end_offset")
            text = norm.get("normalized_text", "")
            if (not source or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or passage.get("source_id") != sid or text[start:end] != passage.get("text")
                or claim.get("claim_text") != passage.get("text")
                or passage.get("artifact_uri") != (norm.get("original_artifact") or {}).get("uri")):
                blockers.append(f"Claim {cid}: exact supporting passage is invalid")
            record, stale = review_state("claim", cid, "analysis_result")
            if record and record["decision"] == "APPROVED_FOR_USE":
                if (source.get("review") or {}).get("decision") == "APPROVED_FOR_USE" and not source.get("is_stale") and not stale:
                    eligible.append(cid)
                else:
                    blockers.append(f"Claim {cid}: supporting source is not currently approved")
            claims.append({**claim, "review": record, "is_stale": stale})
            event("ANALYSIS", analysis.get("analysis_timestamp"), sid, cid,
                  claim.get("original_language"), passage, source.get("requested_url"))
            # Keep differing claim/source publication dates visible, never reconcile them.
            event("CLAIM_PUBLICATION", claim.get("source_published_at"), sid, cid,
                  claim.get("original_language"), passage, source.get("requested_url"))
        if not eligible:
            blockers.append("No approved claims with approved source support")
        if set(normalized) != source_ids:
            blockers.append("Analysis source coverage does not match acquisition")
        for conflict in analysis.get("conflicts", []):
            pair = (conflict.get("claim_id_a"), conflict.get("claim_id_b"))
            if not all(c in claim_ids for c in pair):
                blockers.append("Conflict references missing claims")
            elif not any((reviews["claim"].get(c) or {}).get("decision") == "REJECTED" for c in pair) and not all(
                    (reviews["claim"].get(c) or {}).get("origin") == "AUTOMATED_POLICY"
                    and (reviews["claim"].get(c) or {}).get("decision") != "APPROVED_FOR_USE" for c in pair):
                blockers.append(f"Conflict {conflict.get('group_id')}: explicitly exclude at least one side")
        for kind, records in audit.items():
            for record in records:
                sid = record.get("source_id")
                cid = record.get("claim_id")
                claim = next((c for c in claims if c["claim_id"] == cid), {})
                sid = sid or claim.get("source_id")
                source = sources_by_id.get(sid, {})
                event("REVIEW", record["occurred_at"], sid, cid, claim.get("original_language") or
                      (source.get("normalized") or {}).get("language_detected"), claim.get("passage"), source.get("requested_url"))
                events[-1]["review"] = record
        events.sort(key=lambda e: (e["date"]["utc"] is None, e["date"]["utc"] or "", e["kind"], e["source_id"] or "", e["claim_id"] or ""))
        if analysis.get("failures"):
            limitations.append("Analysis reports failures; inspect referenced analysis artifact")
        limitations.extend(["Source support and human approval do not establish historical truth.",
                            "Duplicate groups and textual similarity are not independent corroboration."])
        selected = [t["target_id"] for t in payloads.get("acquisition_request", {}).get("targets", [])]
        if selected and set(selected) != {s.get("target_id") for s in sources}:
            blockers.append("Acquisition coverage does not match selected targets")
        discovered = payloads.get("discovery_result", {})
        times = [job["created_at"], analysis.get("analysis_timestamp") or job["created_at"]]
        times.extend(r["occurred_at"] for records in audit.values() for r in records)
        body = dict(schema_version=PACKAGE_SCHEMA, policy=POLICY, job_id=job_id, topic=job["topic"], language=job["language"],
                    generated_from_timestamp=max(times), artifacts=refs,
                    discovery={"selected_candidate_ids": selected, "candidates": [c for c in discovered.get("candidates", []) if c["candidate_id"] in selected],
                               "status": discovered.get("status", "UNAVAILABLE"), "providers": discovered.get("providers", [])},
                    sources=sources, claims=claims, duplicate_groups=analysis.get("duplicate_groups", []),
                    claim_groups=analysis.get("claim_groups", []), conflicts=analysis.get("conflicts", []),
                    processing={"status": analysis.get("status", "UNAVAILABLE"), "warnings": analysis.get("warnings", []), "failures": analysis.get("failures", [])},
                    audit=audit, timeline=events, limitations=limitations, blockers=sorted(set(blockers)),
                    eligible_claim_ids=sorted(eligible), ready=not blockers, status="DRAFT", approval=None)
        if policy_rows:
            body["automated_decisions"] = {
                "policy_version": policy_rows[0]["policy_version"], "run_id": policy_rows[0]["run_id"],
                "acquisition_sha256": policy_rows[0]["acquisition_sha256"],
                "analysis_sha256": policy_rows[0]["analysis_sha256"],
                "claims": {r["subject_id"]: {"state": r["state"], "score": r["score"], "claim_class": r["claim_class"],
                                             "independent_groups": r["independent_groups"]}
                           for r in policy_rows if r["subject_type"] == "CLAIM"},
                "sources": {r["subject_id"]: {"state": r["state"]}
                            for r in policy_rows if r["subject_type"] == "SOURCE"}}
        return validate_package(body)

    def draft(self, job_id, owner_id):
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN")
            body = self._snapshot(connection, job_id, owner_id)
            revision = digest(body)
            history = [dict(r) for r in connection.execute(
                "SELECT * FROM evidence_packages WHERE job_id=? ORDER BY version", (job_id,))]
            return {"package": body, "revision": revision, "approvals": history,
                    "current_approval": next((r for r in history if r["revision"] == revision), None)}

    def approve(self, job_id, owner_id, revision, note, idempotency_key, automated=None):
        note = self._validate_note(note)
        if not note:
            raise ReviewValidationError("An approval explanation is required")
        self._validate_idempotency_key(idempotency_key)
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            body = self._snapshot(connection, job_id, owner_id)
            replay = connection.execute("SELECT * FROM evidence_packages WHERE job_id=? AND idempotency_key=?",
                                        (job_id, idempotency_key)).fetchone()
            if replay:
                if replay["revision"] != revision or replay["note"] != note:
                    raise StaleRevision("Idempotency key was used for a different approval")
                return dict(replay)
            if revision != digest(body):
                raise StaleRevision("Evidence or review state changed; reload before approval")
            if body["blockers"]:
                raise StaleRevision("Package is not ready: " + "; ".join(body["blockers"]))
            existing = connection.execute("SELECT * FROM evidence_packages WHERE job_id=? AND revision=?", (job_id, revision)).fetchone()
            if existing:
                return dict(existing)
            version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM evidence_packages WHERE job_id=?", (job_id,)).fetchone()[0]
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            body["status"] = "APPROVED"
            if automated:
                # Automated approval never carries a person's identity.
                body["approval"] = {"revision": revision, "version": version, "approval_kind": "AUTOMATED_POLICY",
                                    "reviewer_id": None, "approved_by": "AUTOMATED_POLICY",
                                    "policy_version": automated["policy_version"], "run_id": automated["run_id"],
                                    "note": note, "approved_at": now}
            else:
                body["approval"] = {"revision": revision, "version": version, "approval_kind": "HUMAN",
                                    "reviewer_id": owner_id, "note": note, "approved_at": now}
            data = canonical(validate_package(body))
            sha = hashlib.sha256(data).hexdigest()
            uri = f"jobs/{job_id}/evidence/{sha}.json"
            self.store.write_bytes(job_id, uri, data, "application/json")
            connection.execute("INSERT INTO evidence_packages VALUES(?,?,?,?,?,?,?,?,?)",
                               (job_id, version, revision, uri, sha, "AUTOMATED_POLICY" if automated else owner_id, note, now, idempotency_key))
            stage_key = "evidence_analysis_and_review"
            stage = connection.execute("SELECT status FROM workflow_stages WHERE job_id=? AND stage_key=?", (job_id, stage_key)).fetchone()
            if connection.execute("SELECT 1 FROM worker_claims WHERE job_id=? AND stage_key=? AND status='ACTIVE'", (job_id, stage_key)).fetchone():
                raise StaleRevision("Evidence stage is owned by an active worker claim")
            if stage and stage[0] in {"PENDING", "RUNNING", "AWAITING_REVIEW"}:
                before = stage[0]
                transitions = {"PENDING": ("RUNNING", "AWAITING_REVIEW", "COMPLETED"),
                               "RUNNING": ("AWAITING_REVIEW", "COMPLETED"), "AWAITING_REVIEW": ("COMPLETED",)}[before]
                for after in transitions:
                    connection.execute("UPDATE workflow_stages SET status=?,version=version+1,updated_at=? WHERE job_id=? AND stage_key=?",
                                       (after, now, job_id, stage_key))
                    connection.execute("INSERT INTO workflow_transitions(job_id,stage_key,from_status,to_status,stage_version,occurred_at) "
                                       "SELECT job_id,stage_key,?,?,version,? FROM workflow_stages WHERE job_id=? AND stage_key=?",
                                       (before, after, now, job_id, stage_key))
                    before = after
                connection.execute("UPDATE workflow_jobs SET status='PAUSED',updated_at=? WHERE job_id=?", (now, job_id))
            connection.commit()
            return dict(connection.execute("SELECT * FROM evidence_packages WHERE job_id=? AND version=?", (job_id, version)).fetchone())

    def export(self, job_id, owner_id, draft=False):
        # Snapshot comparison and ledger lookup share a read transaction. A later
        # review does not rewrite this immutable export; future consumption rechecks.
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN")
            current = self._snapshot(connection, job_id, owner_id)
            if draft:
                return current
            record = connection.execute("SELECT * FROM evidence_packages WHERE job_id=? AND revision=?",
                                        (job_id, digest(current))).fetchone()
            if not record or not current["ready"]:
                raise StaleRevision("No approval for the current evidence and review revision")
            value = self._read_json_artifact(job_id, dict(record))
            validate_package(value)
            approved = value.get("approval", {})
            if value["status"] != "APPROVED" or approved.get("revision") != record["revision"]:
                raise ReviewValidationError("Package approval does not match ledger")
            unsigned = {**value, "status": "DRAFT", "approval": None}
            if digest(unsigned) != record["revision"]:
                raise ReviewValidationError("Package content does not match approved revision")
            return value

    def timeline(self, job_id, owner_id, start=None, end=None, source_id=None, claim_id=None, language=None):
        bounds = []
        for bound in (start, end):
            parsed = timestamp(bound)
            if bound and not parsed["utc"]:
                raise ReviewValidationError("Time-range bounds require ISO timestamps with timezone")
            bounds.append(parsed["utc"])
        if all(bounds) and bounds[0] > bounds[1]:
            raise ReviewValidationError("Time-range start must not exceed end")
        items = self.draft(job_id, owner_id)["package"]["timeline"]
        return {"items": [e for e in items if
            (not source_id or source_id == e["source_id"]) and (not claim_id or claim_id == e["claim_id"]) and
            (not language or language == e["language"]) and
            (not bounds[0] or e["date"]["utc"] and e["date"]["utc"] >= bounds[0]) and
            (not bounds[1] or e["date"]["utc"] and e["date"]["utc"] <= bounds[1])],
            "note": "Undated and timezone-unknown items are excluded by time-range filters; dates are source reports, not verified events."}
