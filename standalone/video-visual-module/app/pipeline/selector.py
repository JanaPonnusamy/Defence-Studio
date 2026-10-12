"""
Asset Selector (spec §3, §7, §8, §11, §34).

Priority (§7):
  1. relevant legitimate source/evidence (EXTERNAL_SOURCE / USER_PROVIDED, or
     SYSTEM_GENERATED maps/stat graphics built from supplied data) with
     usable rights (OPEN / LICENSED / PERMITTED; UNKNOWN only when the plan
     explicitly allows it; RESTRICTED never)
  2. AI-generated illustrative visual -- only for intents where an
     illustration is editorially acceptable (never EVIDENCE, STATISTIC,
     maps/locations, timelines, comparisons)
  3. otherwise an explicit GAP (SOURCE_UNAVAILABLE / RIGHTS_UNKNOWN) or a
     request for generation. AI imagery is never silently substituted for
     missing factual material (§31, §34).

Relevance is a deterministic lexical score between the narration beat and the
asset's title/description/tags (+ entity and number matches). Intents in
STRICT_RELEVANCE_INTENTS require a positive score: an unrelated map, statistic
or document would mislead viewers.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.db.models import Asset
from app.pipeline.intent import STRICT_RELEVANCE_INTENTS, content_tokens

USABLE_RIGHTS = {"OPEN", "LICENSED", "PERMITTED"}
SOURCE_TYPES = {"EXTERNAL_SOURCE", "USER_PROVIDED"}
PLACEHOLDER_TYPE = "PLACEHOLDER_IMAGE"
SYSTEM_GRAPHIC_TYPES = {"MAP", "STAT_CARD", "CHART", "DIAGRAM", "TITLE_CARD"}


@dataclass
class AssetIndex:
    """Pre-computed, per-plan view of a project's assets (keeps planning O(beats x assets) cheap)."""
    assets: list[Asset]
    tokens: dict[str, set[str]] = field(default_factory=dict)
    numbers: dict[str, set[str]] = field(default_factory=dict)
    usage: Counter = field(default_factory=Counter)

    @classmethod
    def build(cls, assets: list[Asset]) -> "AssetIndex":
        idx = cls(assets=[])
        for a in assets:
            idx.add(a)
        return idx

    def add(self, a: Asset) -> None:
        meta = a.extra_metadata or {}
        # normalized metadata used for matching: title, description, tags and optional keyword fields
        extra = []
        for key in ("tags", "keywords", "aliases", "subjects", "location_names", "keywords_i18n"):
            v = meta.get(key)
            if isinstance(v, list):
                extra += [str(x) for x in v]
            elif isinstance(v, dict):  # e.g. {"de": ["Hafen"], "es": ["puerto"]}
                extra += [str(x) for vals in v.values() for x in (vals if isinstance(vals, list) else [vals])]
            elif isinstance(v, str):
                extra.append(v)
        self.tokens[a.asset_id] = content_tokens(f"{a.title or ''} {a.description or ''} {' '.join(extra)}")
        self.numbers[a.asset_id] = {str(n) for n in (meta.get("numbers") or [])}
        self.assets.append(a)


@dataclass
class SelectionResult:
    asset: Asset | None
    tier: int                 # 1 source, 2 AI, 0 none
    relevance: float
    needs_generation: bool
    gap_code: str | None
    editorial_reason: str


def _plannable(a: Asset) -> bool:
    return bool(a.local_path) or a.asset_type == "TRANSITION"


def relevance(index: AssetIndex, a: Asset, beat: dict) -> float:
    toks = index.tokens.get(a.asset_id, set())
    score = float(len(toks & set(beat["tokens"])))
    ent_hits = 0
    for ent in beat["entities"]:
        etoks = content_tokens(ent)
        if etoks and etoks <= toks:
            ent_hits += 1
    score += 2.0 * ent_hits
    if beat["numbers"] and index.numbers.get(a.asset_id):
        score += 5.0 * len(set(beat["numbers"]) & index.numbers[a.asset_id])
    return score


def select_asset(index: AssetIndex, beat: dict, *, allow_unknown_rights: bool = False,
                 avoid_asset_ids: set[str] | None = None) -> SelectionResult:
    intent = beat["primary_intent"]
    preferred: list[str] = beat["preferred_visuals"]
    strict = intent in STRICT_RELEVANCE_INTENTS
    avoid = avoid_asset_ids or set()
    source_types_ok = [t for t in preferred if t != "AI_IMAGE"]

    tier1, unknown_rights_hits = [], 0
    for a in index.assets:
        if a.asset_type not in source_types_ok or not _plannable(a):
            continue
        is_source = a.source_type in SOURCE_TYPES or (a.source_type == "SYSTEM_GENERATED" and a.asset_type in SYSTEM_GRAPHIC_TYPES)
        if not is_source:
            continue
        rel = relevance(index, a, beat)
        if strict and rel <= 0:
            continue
        # never show a statistic graphic whose figure differs from the one being narrated (spec §19/§34)
        if beat["numbers"] and a.asset_type in ("STAT_CARD", "CHART") and index.numbers.get(a.asset_id)                 and not (set(beat["numbers"]) & index.numbers[a.asset_id]):
            continue
        if a.rights_status == "RESTRICTED":
            continue
        if a.rights_status not in USABLE_RIGHTS:
            if not allow_unknown_rights:
                unknown_rights_hits += 1
                continue
        tier1.append((a, rel))
    if tier1:
        # score = lexical relevance + visual-type preference (the intent's preferred type should win
        # unless a lower-ranked type is clearly more specific to the narration).
        # Phase 2: rights-cleared assets ALWAYS rank above UNKNOWN-rights assets, however relevant the
        # latter are -- unknown rights never earn top (evidence) priority.
        n = len(preferred)
        tier1.sort(key=lambda t: (t[0].rights_status not in USABLE_RIGHTS,
                                  -(t[1] + 1.5 * (n - preferred.index(t[0].asset_type))) if t[1] > 0 else 0,
                                  t[0].asset_id in avoid, preferred.index(t[0].asset_type),
                                  index.usage[t[0].asset_id], t[0].asset_id))
        a, rel = tier1[0]
        why = "Relevant legitimate source/evidence asset (priority 1)" if rel > 0 else \
            "Project source asset of the preferred visual type (priority 1, no specific keyword match)"
        if a.rights_status == "UNKNOWN":
            why = ("Source asset with UNKNOWN rights, used only because no rights-cleared alternative exists "
                   "(allowed by plan option; NOT evidence-eligible until rights are cleared)")
        return SelectionResult(a, 1, rel, False, None, why + ".")

    if beat["ai_allowed"]:
        ai = [(a, relevance(index, a, beat)) for a in index.assets
              if a.source_type == "AI_GENERATED" and a.asset_type == "AI_IMAGE" and _plannable(a)
              and a.rights_status != "RESTRICTED"]
        if ai:
            ai.sort(key=lambda t: (-t[1], t[0].asset_id in avoid, index.usage[t[0].asset_id], t[0].asset_id))
            a, rel = ai[0]
            mock = " MOCK/test-fixture" if a.is_mock else ""
            return SelectionResult(a, 2, rel, False, None,
                                   f"No suitable source asset; reusing an existing AI-generated{mock} ILLUSTRATIVE visual "
                                   "(priority 2, not evidence).")
        # Phase 2: placeholders are a separate, last-resort tier -- never counted or labelled as AI
        ph = [(a, relevance(index, a, beat)) for a in index.assets
              if a.asset_type == PLACEHOLDER_TYPE and _plannable(a) and a.rights_status != "RESTRICTED"]
        if ph:
            ph.sort(key=lambda t: (-t[1], t[0].asset_id in avoid, index.usage[t[0].asset_id], t[0].asset_id))
            a, rel = ph[0]
            return SelectionResult(a, 3, rel, False, None,
                                   "No suitable source or AI asset; reusing a PLACEHOLDER image (procedural, not AI, not "
                                   "evidence) - replace before publication.")
        return SelectionResult(None, 0, 0.0, True, "SOURCE_UNAVAILABLE",
                               "No suitable source asset; intent permits an illustrative AI visual (generation requested).")

    if unknown_rights_hits:
        return SelectionResult(None, 0, 0.0, False, "RIGHTS_UNKNOWN",
                               f"{unknown_rights_hits} matching asset(s) have UNKNOWN rights and were not used; clear rights to use them.")
    return SelectionResult(None, 0, 0.0, False, "SOURCE_UNAVAILABLE",
                           f"No relevant {'/'.join(source_types_ok[:3])} source for {intent}; AI substitution refused for factual content.")
