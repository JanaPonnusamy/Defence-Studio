"""Automatic, deterministic evidence validation (policy-driven; replaces per-item clicking).

Reads the job's current acquisition manifest + analysis result, evaluates every source and claim
against ``config/evidence_policy.json`` and the controlled source registry, records one disposition
per subject in the dedicated append-only ``evidence_policy_decisions`` table (policy version + run
id, never a reviewer identity; the human review ledger is never written) and, only when the explicit
readiness criteria pass, records an AUTOMATED_POLICY package approval through the evidence service. Nothing here is a truth test: ``AUTO_ACCEPTED`` means "passed every
mandatory deterministic check", and every non-accepted claim stays out of the usable set.

Safety rules:
  * a person's decision always takes precedence and is never written or altered here;
  * ``NEEDS_REVIEW`` claims are an explicit, explained exclusion from the usable set that does not
    block readiness (distinguished from a person's hold by an explicit ``origin`` field);
  * if readiness criteria fail, nothing is approved and the failed criteria + corrective action
    are returned.
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.acquisition.source_policy import SourcePolicy, default_policy
from defence_studio.contracts.evidence import canonical
from defence_studio.database import connect
from defence_studio.repositories.jobs import JobRepository
from defence_studio.repositories import policy_decisions
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.evidence_readiness import evaluate_readiness, validate_policy_config
from defence_studio.services.job_workspace import JobWorkspaceService
from defence_studio.contracts.analysis import ACQUISITION_RESULT_ARTIFACT, ANALYSIS_RESULT_ARTIFACT
from defence_studio.services.review import ReviewNotFound, ReviewService, ReviewValidationError
from defence_studio.storage.artifacts import ArtifactStore

POLICY_FILE = Path(__file__).resolve().parents[2] / "config" / "evidence_policy.json"
RESULT_SCHEMA = "defence-studio.evidence-policy-result.v1"

AUTO_ACCEPTED, REJECTED, NEEDS_REVIEW, SOURCE_BLOCKED = "AUTO_ACCEPTED", "REJECTED", "NEEDS_REVIEW", "SOURCE_BLOCKED"

_DDL = """CREATE TABLE IF NOT EXISTS evidence_policy_runs (
  job_id TEXT NOT NULL, run_id TEXT NOT NULL, policy_version TEXT NOT NULL,
  result_uri TEXT NOT NULL, result_sha256 TEXT NOT NULL, counts_json TEXT NOT NULL,
  ready INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id, run_id))"""

_PUBLISHED_META = re.compile(
    r"""<meta[^>]+(?:property|name|itemprop)\s*=\s*["'](?:article:published_time|og:article:published_time|"""
    r"""datePublished|pubdate|publishdate|date|dc\.date(?:\.issued)?)["'][^>]*content\s*=\s*["']([^"']{6,40})["']""",
    re.IGNORECASE)
_PUBLISHED_META_REV = re.compile(
    r"""<meta[^>]+content\s*=\s*["']([^"']{6,40})["'][^>]*(?:property|name|itemprop)\s*=\s*["']"""
    r"""(?:article:published_time|og:article:published_time|datePublished|pubdate|publishdate|date)["']""",
    re.IGNORECASE)
_TIME_TAG = re.compile(r"""<time[^>]+datetime\s*=\s*["']([^"']{6,40})["']""", re.IGNORECASE)
_JSONLD_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]{6,40})"')
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
_WS = re.compile(r"\s+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def load_evidence_policy(path: Path | None = None) -> dict:
    data = json.loads(Path(path or POLICY_FILE).read_text(encoding="utf-8"))
    return validate_policy_config(data)


def _norm_text(text: str) -> str:
    text = re.sub(r"^\s*\d{1,3}\s+(?=[A-Z])", "", text)         # leading footnote number
    return _WS.sub(" ", text).strip().lower()


def extract_publication_date(html: str) -> tuple[str | None, str]:
    """Return (ISO date or None, basis). Never guesses: only explicit markup is accepted."""
    for label, rx in (("HTML_META", _PUBLISHED_META), ("HTML_META", _PUBLISHED_META_REV),
                      ("JSON_LD", _JSONLD_DATE), ("TIME_TAG", _TIME_TAG)):
        for m in rx.finditer(html[:400_000]):
            if _ISO.match(m.group(1).strip()):
                return m.group(1).strip()[:10], label
    return None, "UNKNOWN"


class EvidencePolicyService:
    def __init__(self, database_file: Path, storage_root: Path, *, policy: dict | None = None,
                 source_policy: SourcePolicy | None = None, workspace: JobWorkspaceService | None = None):
        self.database_file = Path(database_file)
        self.storage_root = Path(storage_root)
        self.store = ArtifactStore(self.storage_root)
        self.policy = validate_policy_config(policy) if policy is not None else load_evidence_policy()
        self.sources = source_policy if source_policy is not None else default_policy()
        self.review = ReviewService(self.database_file, self.storage_root)
        self.evidence = EvidenceService(self.database_file, self.storage_root)
        self.workspace = workspace or JobWorkspaceService(self.database_file)
        with closing(connect(self.database_file)) as c:
            c.execute(_DDL)
            policy_decisions.ensure(c)
            c.commit()

    # -- inputs --------------------------------------------------------------
    def _load(self, job_id: str, owner_id: str):
        with closing(connect(self.database_file)) as c:
            job = JobRepository(c).get_owned_job(job_id, owner_id)
            if job is None:
                raise ReviewNotFound("Workflow job not found")
            acq_ref = ReviewService._latest_artifact(c, job_id, ACQUISITION_RESULT_ARTIFACT)
            ana_ref = ReviewService._latest_artifact(c, job_id, ANALYSIS_RESULT_ARTIFACT)
        if acq_ref is None or ana_ref is None:
            raise ReviewValidationError("Acquisition and analysis results must exist before the evidence policy runs")
        return (dict(job), acq_ref, ana_ref,
                self.review._read_json_artifact(job_id, acq_ref), self.review._read_json_artifact(job_id, ana_ref))

    def _anchors(self, topic: str) -> list[str]:
        """Distinctive topic terms derived from the job's own topic text (no per-topic code).

        Optional ``relevance.topic_profiles`` (config) may ADD aliases for a topic. Generic words come
        from ``relevance.anchor_stopwords``. Hyphenated designators also match their compact form
        (``S-400`` -> ``s400``)."""
        low = (topic or "").lower()
        rel = self.policy["relevance"]
        stop = {w.lower() for w in rel.get("anchor_stopwords", [])}
        out: set[str] = set()
        for tok in re.findall(r"[\w][\w.-]*[\w]|[\w]", low):
            if tok in stop or len(tok) < 4 and not any(ch.isdigit() for ch in tok):
                continue
            out.add(tok)
            if "-" in tok:
                out.add(tok.replace("-", ""))
        for prof in rel.get("topic_profiles", []):
            if re.search(prof["match"], low):
                out.update(a.lower() for a in prof.get("anchors", []))
        return sorted(out)

    def subject_categories(self, topic: str) -> list[str]:
        low = (topic or "").lower()
        found = []
        for cat in self.policy.get("subject_categories", []):
            if any(re.search(r"\b" + re.escape(k.lower()) + r"\b", low) for k in cat.get("keywords", [])):
                found.append(cat["id"])
        return found

    # -- evaluation ----------------------------------------------------------
    def evaluate(self, job_id: str, owner_id: str) -> dict:
        job, acq_ref, ana_ref, acq, ana = self._load(job_id, owner_id)
        anchors = self._anchors(job["topic"])
        subjects = self.subject_categories(job["topic"])
        cfg = self.policy
        normalized = {n["source_id"]: n for n in ana.get("normalized_sources", []) if n.get("status") == "NORMALIZED"}

        # ---- sources
        source_out, usable = [], {}
        for s in acq.get("sources", []):
            sid = s["source_id"]
            reasons, state, entry = [], AUTO_ACCEPTED, None
            urls = [u for u in (s.get("requested_url"), s.get("canonical_url"), s.get("final_url")) if u]
            if self.sources is not None:
                entries = [self.sources.entry_for(u) for u in urls]
                if not urls or any(e is None for e in entries) or len({e.id for e in entries}) != 1:
                    state, reasons = SOURCE_BLOCKED, ["URL (requested/canonical/final) is not permitted by the source registry"]
                else:
                    entry = entries[0]
            if state == AUTO_ACCEPTED:
                err = (s.get("error") or "")
                norm = normalized.get(sid)
                if s.get("status") != "ACQUIRED":
                    state = SOURCE_BLOCKED if "SOURCE_NOT_ALLOWLISTED" in str(err) else REJECTED
                    reasons.append(f"acquisition status {s.get('status')}: {str(err)[:160]}")
                elif norm is None:
                    state, reasons = REJECTED, ["content could not be normalized"]
                else:
                    try:
                        for ref in s.get("artifacts", []):
                            if self.store.sha256_of(job_id, ref["uri"]) != ref["sha256"]:
                                raise ValueError("hash mismatch")
                    except (OSError, ValueError, KeyError):
                        state, reasons = REJECTED, ["stored content failed its integrity (sha256) check"]
            if state == AUTO_ACCEPTED and entry is not None:
                if entry.supported_subjects and subjects and not (set(entry.supported_subjects) & set(subjects)):
                    state = REJECTED
                    reasons.append("source is not configured for this topic's subject categories "
                                   f"({sorted(subjects)}; source supports {sorted(entry.supported_subjects)})")
                media = ((s.get("artifacts") or [{}])[0].get("media_type") or "").split(";")[0].strip().lower()
                if state == AUTO_ACCEPTED and entry.allowed_content_types and media and \
                        media not in entry.allowed_content_types:
                    state = REJECTED
                    reasons.append(f"content type {media} is not permitted for this source")
            date, basis, mentions = None, "UNKNOWN", 0
            if state == AUTO_ACCEPTED:
                text = normalized[sid]["normalized_text"].lower()
                mentions = sum(text.count(a) for a in anchors) if anchors else 0
                need = int(cfg["relevance"]["min_source_anchor_mentions"])
                if anchors and mentions < need:
                    state = REJECTED
                    reasons.append(f"not relevant to the topic: {mentions} mention(s) of topic anchors, policy minimum {need}")
                try:
                    art = s["artifacts"][0]
                    html = self.store.read_bytes(job_id, art["uri"], max_bytes=16 * 1024 * 1024).decode("utf-8", "replace")
                    date, basis = extract_publication_date(html)
                except (OSError, KeyError, IndexError, ValueError):
                    pass
            if state == AUTO_ACCEPTED:
                reasons.append("allowlisted, acquired, integrity verified, topic-relevant")
            row = {"source_id": sid, "state": state, "reasons": reasons, "registry_id": entry.id if entry else None,
                   "category": entry.category if entry else None, "publisher": entry.publisher if entry else None,
                   "publisher_basis": "SOURCE_REGISTRY" if entry and entry.publisher else "UNKNOWN",
                   "independence_group": entry.independence_group if entry else None,
                   "quantitative_trust": entry.quantitative_trust if entry else None,
                   "published_at": date, "published_at_basis": basis, "anchor_mentions": mentions,
                   "canonical_url": s.get("canonical_url"), "final_url": s.get("final_url"),
                   "retrieved_at": s.get("acquisition_timestamp"),
                   "sha256": (s.get("artifacts") or [{}])[0].get("sha256")}
            source_out.append(row)
            if state == AUTO_ACCEPTED:
                usable[sid] = row

        # ---- claims
        conflict_ids = set()
        for c in ana.get("conflicts", []):
            conflict_ids.update(x for x in (c.get("claim_id_a"), c.get("claim_id_b")) if x)
        claim_cfg = cfg["claim"]
        bib = [re.compile(p) for p in claim_cfg["bibliography_patterns"]]
        unc = re.compile(r"\b(?:" + "|".join(claim_cfg["uncertainty_markers"]) + r")\b", re.IGNORECASE)
        tsens = re.compile("|".join(re.escape(m) if m.endswith(" ") else r"\b" + re.escape(m) + r"\b"
                                    for m in claim_cfg["time_sensitive_markers"]), re.IGNORECASE)
        claims = ana.get("claims", [])
        # quantity corroboration index: normalized number tokens -> independence groups
        def _group_of(src):
            if src.get("independence_group"):
                return src["independence_group"]
            host = urlsplit(src.get("final_url") or src.get("canonical_url") or "").hostname
            return "host:" + (host or src["source_id"])

        manufacturer_groups = {_group_of(u) for u in usable.values() if u.get("category") == "MANUFACTURER"}

        def qty_tokens(c):
            return {re.sub(r"[^\d.]", "", q) for q in (c.get("entities") or {}).get("quantities", [])} - {""}
        groups_by_qty: dict[str, set] = {}
        sources_by_qty: dict[str, set] = {}
        for c in claims:
            src = usable.get(c["source_id"])
            if not src:
                continue
            for t in qty_tokens(c):
                groups_by_qty.setdefault(t, set()).add(_group_of(src))
                sources_by_qty.setdefault(t, set()).add(c["source_id"])
        strict_rx = re.compile("|".join(cfg["strict_claims"]["patterns"]), re.IGNORECASE)
        strict_min = int(cfg["strict_claims"]["min_independent_groups"])
        score_cfg = cfg["scoring"]
        conflict_peers: dict[str, list] = {}
        for cf in ana.get("conflicts", []):
            a, b = cf.get("claim_id_a"), cf.get("claim_id_b")
            if a and b:
                conflict_peers.setdefault(a, []).append(b)
                conflict_peers.setdefault(b, []).append(a)
        seen_text: dict = {}
        first_by_norm: dict[str, str] = {}
        class_cfgs = cfg.get("claim_classes", [])
        class_rx = [(k, re.compile("|".join(k.get("patterns", [])), re.IGNORECASE) if k.get("patterns") else None)
                    for k in class_cfgs]
        attr_rx = re.compile("|".join(cfg["claim"].get("attribution_markers",
                                                      ["according to", "said", "stated", "announced", "reported"])),
                             re.IGNORECASE)
        sim_min = float(cfg["corroboration"].get("similarity_threshold", 0.5))
        stop = {w.lower() for w in cfg["relevance"].get("anchor_stopwords", [])}

        def content_words(t):
            return {w for w in re.findall(r"[a-z0-9][a-z0-9-]{3,}", t.lower()) if w not in stop}
        words_by_claim = {c["claim_id"]: content_words(c["claim_text"]) for c in claims}

        def textual_groups(c):
            """Independent, non-manufacturer source groups that make a textually similar statement."""
            mine = words_by_claim[c["claim_id"]]
            groups = set()
            for o in claims:
                src_o = usable.get(o["source_id"])
                if not src_o or src_o.get("category") == "MANUFACTURER":
                    continue
                other = words_by_claim[o["claim_id"]]
                union = mine | other
                if union and len(mine & other) / len(union) >= sim_min:
                    groups.add(_group_of(src_o))
            return groups

        def classify(text, has_qty):
            for k, rx in class_rx:
                if k.get("needs_quantity") and not has_qty:
                    continue
                if rx is not None and rx.search(text):
                    return k
            return None
        claim_out = []
        qmin = int(cfg["corroboration"]["quantitative_min_independent_groups"])
        for c in claims:
            cid, sid, text = c["claim_id"], c["source_id"], c["claim_text"]
            src_row = next((r for r in source_out if r["source_id"] == sid), None)
            state, reasons, flags = AUTO_ACCEPTED, [], []
            passage = c.get("passage") or {}
            norm = normalized.get(sid, {})
            nt = norm.get("normalized_text", "")
            s0, e0 = passage.get("start_offset"), passage.get("end_offset")
            if src_row is None or src_row["state"] != AUTO_ACCEPTED:
                state = SOURCE_BLOCKED if src_row and src_row["state"] == SOURCE_BLOCKED else REJECTED
                reasons.append("supporting source is not usable under the source policy")
            elif not (type(s0) is int and type(e0) is int and 0 <= s0 < e0 <= len(nt) and nt[s0:e0] == text == passage.get("text")):
                state, reasons = REJECTED, ["supporting passage does not match the stored source text"]
            elif len(re.findall(r"\w+", text)) < int(claim_cfg["min_words"]):
                state, reasons = REJECTED, [f"fragment: fewer than {claim_cfg['min_words']} words"]
            elif any(rx.search(text) for rx in bib):
                state, reasons = REJECTED, ["bibliography / citation entry, not a factual statement"]
            elif anchors and not any(a in text.lower() for a in anchors):
                state, reasons = REJECTED, ["no topic anchor term: not self-contained (refers to the topic only by pronoun or context), so it is not used as standalone evidence"]
            else:
                norm_key = _norm_text(text)
                # Duplicates are collapsed WITHIN one independence group (syndication/page repeats); an identical
                # statement from a different independent group is corroboration, not a duplicate.
                key = ((src_row.get("independence_group") or "host:" + sid), norm_key)
                if key in seen_text:
                    state, reasons = REJECTED, [f"duplicate of {seen_text[key]}"]
                else:
                    seen_text[key] = cid
                    first = first_by_norm.setdefault(norm_key, cid)
                    if first != cid:
                        flags.append("corroborates:" + first)
                    if cid in conflict_ids:
                        state, reasons = NEEDS_REVIEW, ["participates in a detected conflict; not resolved automatically"]
                    elif unc.search(text):
                        state, reasons = NEEDS_REVIEW, ["states uncertainty or a dispute; not accepted automatically"]
                    elif tsens.search(text) and not src_row.get("published_at"):
                        state, reasons = NEEDS_REVIEW, ["time-sensitive statement from a source with unknown publication date"]
                    else:
                        qs = qty_tokens(c)
                        klass = classify(text, bool(qs))
                        if klass is not None and not qs and int(klass.get("min_independent_groups", 1)) > 1:
                            need = int(klass["min_independent_groups"])
                            ng = len(textual_groups(c))
                            attributed_text = bool(c.get("qualifiers")) or bool(attr_rx.search(text))
                            if ng < need and not (klass.get("attribution_ok") and attributed_text):
                                state, reasons = NEEDS_REVIEW, [
                                    f"{klass['id']} claim: {ng} independent source group(s), policy requires "
                                    f"{need} or explicit attribution"]
                            else:
                                flags.append("class:" + klass["id"])
                                reasons.append(f"{klass['id']} claim: corroborated by {ng} group(s)"
                                               if ng >= need else f"{klass['id']} claim: explicitly attributed")
                        elif qs:
                            trust = src_row["quantitative_trust"]
                            groups = set().union(*(groups_by_qty.get(q, set()) for q in qs))
                            attributed = bool(c.get("qualifiers"))
                            if trust == "NOT_TRUSTED":
                                state, reasons = NEEDS_REVIEW, ["figures from this source category are not trusted automatically"]
                            elif trust == "ATTRIBUTED_ONLY" and not attributed:
                                state, reasons = NEEDS_REVIEW, ["figure without explicit attribution"]
                            elif trust == "CORROBORATION_REQUIRED" and len(groups) < qmin:
                                state, reasons = NEEDS_REVIEW, [
                                    f"single-source figure: {len(groups)} independent source group(s), policy requires {qmin}"]
                            else:
                                flags.append("quantitative")
                                reasons.append(f"figure corroborated by {len(groups)} independent group(s)"
                                               if trust == "CORROBORATION_REQUIRED" else "attributed figure")
                        if state == AUTO_ACCEPTED:
                            reasons.insert(0, "substantive, topic-relevant, source usable, no uncertainty or dispute markers")
            if state == AUTO_ACCEPTED and src_row is not None and qty_tokens(c) and strict_rx.search(text):
                # military performance / casualty / operational claim: stricter class
                qs = qty_tokens(c)
                flags.append("strict_class")
                ind = {g for q in qs for g in groups_by_qty.get(q, set()) if g not in manufacturer_groups}
                if src_row["category"] == "MANUFACTURER":
                    state, reasons = NEEDS_REVIEW, ["manufacturer claim; not independently verified performance"]
                elif len(ind) < strict_min:
                    state, reasons = NEEDS_REVIEW, [
                        f"performance/operational claim: {len(ind)} independent non-manufacturer group(s), strict policy requires {strict_min}"]
                else:
                    reasons.append(f"strict class: figure corroborated by {len(ind)} independent group(s)")
            score = self._score(c, src_row, nt, anchors, groups_by_qty, qty_tokens(c), unc, tsens,
                                conflict_peers.get(cid, []), score_cfg)
            if state == AUTO_ACCEPTED and score["total"] < int(score_cfg["auto_accept_min_score"]):
                state = NEEDS_REVIEW
                reasons = [f"score {score['total']} below policy threshold {score_cfg['auto_accept_min_score']}"]
            klass_row = classify(text, bool(qty_tokens(c)))
            claim_class = klass_row["id"] if klass_row else ("QUANTITATIVE" if qty_tokens(c) else "DESCRIPTIVE")
            n_groups = 0
            if src_row and src_row["state"] == AUTO_ACCEPTED:
                fig = {g for q in qty_tokens(c) for g in groups_by_qty.get(q, set()) if g not in manufacturer_groups}
                n_groups = max(len(textual_groups(c)), len(fig))
            if c.get("qualifiers"):
                flags.append("qualified:" + ",".join(c["qualifiers"]))
            if re.match(r"^\s*\d{1,3}\s+[A-Z]", text):
                flags.append("footnote_number_prefix")
            claim_out.append({"claim_id": cid, "source_id": sid, "state": state, "reasons": reasons, "flags": flags,
                              "text": text, "passage": {"start": s0, "end": e0}, "score": score,
                              "claim_class": claim_class, "independent_groups": n_groups,
                              "supporting_evidence": ([{"source_id": sid, "start": s0, "end": e0}]
                                                      if score["components"]["passage_match"] else []),
                              "contradicting_evidence": [{"claim_id": x} for x in conflict_peers.get(cid, [])],
                              "corroboration": self._corroboration(qty_tokens(c), groups_by_qty, sources_by_qty)})

        counts = {k: sum(1 for c in claim_out if c["state"] == k) for k in (AUTO_ACCEPTED, REJECTED, NEEDS_REVIEW, SOURCE_BLOCKED)}
        scounts = {k: sum(1 for s in source_out if s["state"] == k) for k in (AUTO_ACCEPTED, REJECTED, SOURCE_BLOCKED)}
        # a source is "accepted" for readiness only if it still supports at least one accepted claim
        supporting = {c["source_id"] for c in claim_out if c["state"] == AUTO_ACCEPTED}
        failed = evaluate_readiness(
            cfg, accepted_claims=len({_norm_text(c["text"]) for c in claim_out if c["state"] == AUTO_ACCEPTED}),
            supporting_sources=supporting,
            supporting_categories={usable[x]["category"] for x in supporting if x in usable},
            missing_publisher_sources={x for x in supporting if x in usable and not usable[x].get("publisher")},
            anchors=anchors, stale_decisions=False, versions={cfg["version"]},
            unresolved_conflicts=[], claims_without_disposition=sum(1 for c in claim_out if not c.get("state")))
        body = {"schema_version": RESULT_SCHEMA, "policy_version": cfg["version"], "scoring_policy_version": cfg["scoring"]["version"],
                "source_policy_version": self.sources.version if self.sources else "DISABLED",
                "analysis_versions": ana.get("versions"), "job_id": job_id, "topic": job["topic"], "anchors": anchors,
                "subject_categories": subjects,
                "acquisition_sha256": acq_ref["sha256"], "analysis_sha256": ana_ref["sha256"],
                "sources": source_out, "claims": claim_out,
                "counts": {"claims": counts, "sources": scounts},
                "policy_criteria_failed": failed}
        return body

    @staticmethod
    def _corroboration(qs, groups_by_qty, sources_by_qty) -> dict:
        """Which independence groups / sources repeat the same figure. Descriptive claims have no
        checkable figure, so their corroboration is reported as not applicable, never invented."""
        if not qs:
            return {"applicable": False, "quantities": [], "independent_groups": [], "source_ids": [],
                    "single_source": None}
        groups = sorted(set().union(*(groups_by_qty.get(q, set()) for q in qs)))
        sids = sorted(set().union(*(sources_by_qty.get(q, set()) for q in qs)))
        return {"applicable": True, "quantities": sorted(qs), "independent_groups": groups,
                "source_ids": sids, "single_source": len(groups) <= 1}

    @staticmethod
    def _score(c, src_row, nt, anchors, groups_by_qty, qs, unc, tsens, peers, cfg) -> dict:
        """Deterministic 0-100 score. Components are 0..1 and unknown stays 0 (never fabricated).
        The score is NOT a probability of truth and can never override a failed mandatory check."""
        text, passage = c["claim_text"], c.get("passage") or {}
        ok_src = bool(src_row) and src_row["state"] == AUTO_ACCEPTED
        s0, e0 = passage.get("start_offset"), passage.get("end_offset")
        match = type(s0) is int and type(e0) is int and 0 <= s0 < e0 <= len(nt) and nt[s0:e0] == text
        cat = (src_row or {}).get("category")
        comp = {
            "source_credibility": cfg["category_credibility"].get(cat, 0.0) if ok_src else 0.0,
            "relevance": 1.0 if anchors and any(a in text.lower() for a in anchors) else 0.0,
            "passage_match": 1.0 if match else 0.0,
        }
        groups = set().union(*(groups_by_qty.get(q, set()) for q in qs)) if qs else set()
        comp["corroboration"] = min(len(groups), 2) / 2 if qs else (0.5 if ok_src else 0.0)
        if tsens.search(text):
            comp["freshness"] = 1.0 if (src_row or {}).get("published_at") else 0.0
        else:
            comp["freshness"] = 1.0 if ok_src else 0.0
        comp["attribution"] = 0.0 if unc.search(text) else (1.0 if (c.get("qualifiers") or not qs) else 0.5)
        comp["contradiction_clear"] = 0.0 if peers else 1.0
        weights = cfg["weights"]
        total = round(sum(comp[k] * weights[k] for k in weights), 1)
        return {"total": total, "scale": "0-100", "components": comp, "weights": weights,
                "scoring_policy_version": cfg["version"], "calibrated": False}

    # -- apply ---------------------------------------------------------------
    def run(self, job_id: str, owner_id: str, *, approve_package: bool = True) -> dict:
        body = self.evaluate(job_id, owner_id)
        digest = hashlib.sha256(canonical(body)).hexdigest()
        run_id = digest[:16]
        self.workspace.write_text(job_id, f"policy/{run_id}.input.json",
                                  json.dumps({"counts": body["counts"], "analysis_sha256": body["analysis_sha256"]}))
        human = {"claim": {c["claim_id"]: c.get("review") for c in self.review.list_claims(job_id, owner_id)["claims"]},
                 "source": {x["source_id"]: x.get("review") for x in self.review.list_sources(job_id, owner_id)["sources"]}}
        skipped_human = [{"subject": sid, "decision": rec["decision"]}
                         for kind in ("claim", "source") for sid, rec in human[kind].items()
                         if rec and rec.get("origin", "HUMAN") == "HUMAN"]
        rows = [{"subject_type": "SOURCE", "subject_id": x["source_id"], "state": x["state"], "reasons": x["reasons"],
                 "evidence": {"canonical_url": x["canonical_url"], "sha256": x["sha256"],
                              "independence_group": x["independence_group"], "category": x["category"]}}
                for x in body["sources"]]
        rows += [{"subject_type": "CLAIM", "subject_id": x["claim_id"], "state": x["state"], "reasons": x["reasons"],
                  "score": x["score"]["total"], "components": x["score"]["components"],
                  "claim_class": x.get("claim_class"), "independent_groups": x.get("independent_groups"),
                  "evidence": {"source_id": x["source_id"], "passage": x["passage"],
                               "supporting": x.get("supporting_evidence"), "contradicting": x.get("contradicting_evidence")}}
                for x in body["claims"]]
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            policy_decisions.insert_run(c, job_id, run_id, self.policy["version"], self.policy["scoring"]["version"],
                                        body["acquisition_sha256"], body["analysis_sha256"], rows, _now())
            c.commit()

        draft = self.evidence.draft(job_id, owner_id)
        pkg = draft["package"]
        blockers = list(pkg["blockers"])
        approval = None
        ready = not blockers and not body["policy_criteria_failed"]
        if ready and approve_package:
            existing = draft.get("current_approval")
            if existing:
                approval = self._approval_view(existing)
            else:
                row = self.evidence.approve(
                    job_id, owner_id, draft["revision"],
                    f"[AUTO-POLICY {self.policy['version']}] readiness criteria satisfied: "
                    f"{body['counts']['claims'][AUTO_ACCEPTED]} claims accepted", f"policy-approve-{run_id}-r{draft['revision']}",
                    automated={"policy_version": self.policy["version"], "run_id": run_id})
                approval = self._approval_view(row)
        result = {**body, "run_id": run_id, "package_blockers": blockers, "human_decisions_preserved": skipped_human,
                  "package_ready": not blockers, "ready_for_generation": ready and approval is not None,
                  "package_approval": approval}
        if not result["ready_for_generation"]:
            result["explanation"] = ("Generation is stopped: " + "; ".join(
                [f"{f['criterion']} required {f['required']} got {f['actual']}" for f in body["policy_criteria_failed"]]
                + blockers[:5]) or "package not approved")
        data = canonical(result)
        sha = hashlib.sha256(data).hexdigest()
        uri = f"jobs/{job_id}/evidence/policy_{sha}.json"
        self.store.write_bytes(job_id, uri, data, "application/json")
        with closing(connect(self.database_file)) as c:
            c.execute("INSERT OR REPLACE INTO evidence_policy_runs VALUES(?,?,?,?,?,?,?,?)",
                      (job_id, run_id, self.policy["version"], uri, sha, json.dumps(body["counts"]),
                       int(result["ready_for_generation"]), _now()))
            c.commit()
        result["result_uri"], result["result_sha256"] = uri, sha
        return result

    @staticmethod
    def _approval_view(row) -> dict:
        row = dict(row)
        return {"version": row.get("version"), "revision": row.get("revision"),
                "reviewer_id": row.get("reviewer_id"), "approval_kind":
                    "AUTOMATED_POLICY" if row.get("reviewer_id") == "AUTOMATED_POLICY" else "HUMAN",
                "approved_at": row.get("approved_at") or row.get("created_at")}

    def latest(self, job_id: str, owner_id: str) -> dict | None:
        with closing(connect(self.database_file)) as c:
            if JobRepository(c).get_owned_job(job_id, owner_id) is None:
                raise ReviewNotFound("Workflow job not found")
            row = c.execute("SELECT * FROM evidence_policy_runs WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
                            (job_id,)).fetchone()
        if not row:
            return None
        data = json.loads(self.store.read_bytes(job_id, row["result_uri"]))
        data["result_uri"], data["result_sha256"] = row["result_uri"], row["result_sha256"]
        return data
