"""
Meaning / Visual Intent stage (spec §3, §12, §14).

Deterministic, offline heuristic classifier (no LLM call): each narration
segment is mapped to visual intents, a ranked list of preferred visual asset
types, extracted entities and numbers, and whether an AI illustration is
editorially acceptable for it (spec §8/§34: never for evidence, statistics,
maps/locations, timelines or comparisons).
"""
from __future__ import annotations

import re

VISUAL_INTENTS = [
    "GEOGRAPHIC_CONTEXT", "ENTITY_INTRODUCTION", "EVENT_EXPLANATION", "TIMELINE",
    "COMPARISON", "STATISTIC", "EVIDENCE", "HARDWARE_DETAIL", "MILITARY_CAPABILITY",
    "LOCATION", "ESCALATION", "CONCLUSION",
]

# intent -> (ranked preferred asset types, ai illustration allowed)
INTENT_VISUALS: dict[str, tuple[list[str], bool]] = {
    "GEOGRAPHIC_CONTEXT": (["MAP", "SATELLITE", "SOURCE_PHOTO", "IMAGE"], False),
    "LOCATION": (["MAP", "SATELLITE", "SOURCE_PHOTO", "IMAGE"], False),
    "ENTITY_INTRODUCTION": (["SOURCE_PHOTO", "IMAGE", "VIDEO_CLIP", "TITLE_CARD"], False),
    "EVENT_EXPLANATION": (["SOURCE_PHOTO", "IMAGE", "VIDEO_CLIP", "DIAGRAM", "AI_IMAGE"], True),
    "TIMELINE": (["CHART", "STAT_CARD", "DIAGRAM"], False),
    "COMPARISON": (["CHART", "STAT_CARD", "DIAGRAM"], False),
    "STATISTIC": (["STAT_CARD", "CHART"], False),
    "EVIDENCE": (["DOCUMENT", "SATELLITE", "SOURCE_PHOTO", "IMAGE", "VIDEO_CLIP"], False),
    "HARDWARE_DETAIL": (["SOURCE_PHOTO", "IMAGE", "VIDEO_CLIP", "DIAGRAM", "AI_IMAGE"], True),
    "MILITARY_CAPABILITY": (["SOURCE_PHOTO", "IMAGE", "VIDEO_CLIP", "MAP", "AI_IMAGE"], True),
    "ESCALATION": (["SOURCE_PHOTO", "IMAGE", "VIDEO_CLIP", "AI_IMAGE"], True),
    "CONCLUSION": (["SOURCE_PHOTO", "IMAGE", "AI_IMAGE", "TITLE_CARD"], True),
}
# intents whose visuals must be specifically relevant (an unrelated map/stat/document would mislead)
STRICT_RELEVANCE_INTENTS = {"EVIDENCE", "STATISTIC", "GEOGRAPHIC_CONTEXT", "LOCATION", "TIMELINE", "COMPARISON"}

_KEYWORDS: dict[str, list[str]] = {
    "STATISTIC": [r"percent", r"per cent", r"\d+(\.\d+)?\s?%", r"number of", r"casualt\w*", r"tonnes?",
                  r"\d[\d,.]*\s?(km|kilometres?|kilometers?|miles|billion|million|thousand|troops|units|aircraft|ships|tanks|dollars|usd)"],
    # attribution alone ("according to monitors") is not evidence; an evidentiary object must be referenced
    "EVIDENCE": [r"satellite (imagery|images|photos?)", r"documents?", r"leaked", r"evidence", r"footage",
                 r"(official|written|signed) (release|report|statement|order)", r"report(s|ed)? (show|say|state)\w*",
                 r"according to (a|an|the) ([\w-]+ )?(report|statement|release|filing|memo|order)"],
    "GEOGRAPHIC_CONTEXT": [r"near", r"border\w*", r"region\w*", r"sea", r"coast\w*", r"territor\w*", r"concentrated",
                           r"north\w*", r"south\w*", r"strait", r"peninsula", r"gulf"],
    "LOCATION": [r"located", r"city", r"base", r"port", r"airfield", r"airbase", r"harbou?r", r"province"],
    "COMPARISON": [r"compared (to|with)", r"versus", r"\bvs\.?", r"more than", r"fewer than", r"twice as", r"outnumber\w*"],
    "TIMELINE": [r"since", r"timeline", r"over the past", r"months? ago", r"years? ago", r"in \d{4}", r"by \d{4}"],
    "HARDWARE_DETAIL": [r"missiles?", r"tanks?", r"aircraft", r"warheads?", r"radars?", r"drones?", r"fighter jets?",
                        r"submarines?", r"howitzers?", r"launchers?", r"frigates?", r"destroyers?"],
    "MILITARY_CAPABILITY": [r"fleet", r"brigades?", r"divisions?", r"squadrons?", r"capabilit\w*", r"forces?", r"battalions?"],
    "ESCALATION": [r"escalat\w*", r"strikes?", r"attack\w*", r"retaliat\w*", r"offensive", r"shelling", r"invasion"],
    "ENTITY_INTRODUCTION": [r"was founded", r"known as", r"led by", r"commander of", r"minister", r"president",
                            r"chief of", r"is (a|an|the) (company|manufacturer|organi[sz]ation|group|unit|leader|politician)"],
    "EVENT_EXPLANATION": [r"happened", r"occurred", r"began when", r"because", r"as a result"],
    "CONCLUSION": [r"in summary", r"ultimately", r"in conclusion", r"what comes next", r"to sum up", r"the question now"],
}
_COMPILED = {
    k: [re.compile((r"\b" if p[0].isalpha() else "") + rf"(?:{p})(?!\w)", re.I) for p in v]
    for k, v in _KEYWORDS.items()
}
# priority used to pick the primary intent when several match
_PRIORITY = ["STATISTIC", "EVIDENCE", "COMPARISON", "TIMELINE", "GEOGRAPHIC_CONTEXT", "LOCATION", "CONCLUSION",
             "HARDWARE_DETAIL", "MILITARY_CAPABILITY", "ESCALATION", "ENTITY_INTRODUCTION", "EVENT_EXPLANATION"]

_STOP = set("""the a an and or but of in on at to for from by with as is are was were be been this that these those it its
their there here into over under after before during while about against between through would could should will
can may might has have had not no yes than then them they he she we you our your his her which who whom what when
where why how also more most such some any each every other only very just because""".split()) | {
    # domain-generic words: too common in defence narration to signal a SPECIFIC visual match
    "force", "forces", "unit", "units", "area", "areas", "region", "regional", "military", "officials", "official",
    "year", "years", "last", "week", "weeks", "month", "months", "roughly", "around", "near", "said", "says",
    "say", "new", "several", "recent", "current", "currently", "analysts", "expect", "situation"}
_ENTITY_RE = re.compile(r"\b([A-Z][a-zA-Z\-]+(?:\s+(?:of\s+the\s+|of\s+|de\s+)?[A-Z][a-zA-Z\-]+)*)")
_ENTITY_STOP = {"The", "A", "An", "In", "On", "At", "But", "And", "This", "That", "These", "Those", "It", "Its",
                "According", "Today", "Yesterday", "However", "Meanwhile", "Ultimately", "What", "When", "Where",
                "Why", "How", "If", "As", "By", "For", "From", "With", "After", "Before", "Since", "Over", "Now", "So",
                "They", "We", "He", "She", "Our", "Their", "There", "Here", "Still", "Then"}
_NUMBER_RE = re.compile(r"(?<![\w.\-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s?(%|percent|per cent|km|billion|million|thousand))?", re.I)


# Phase 2 matching: Unicode-aware tokens (any script), accent folding, and a synonym/variant table that maps
# equivalent words (incl. plural forms and a few non-English equivalents) to a shared concept token "~concept".
# For plain-ASCII English text the base tokens are identical to v1.0; concept tokens are added on top.
_TOKEN_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|-){2,}")
SYNONYMS: dict[str, list[str]] = {
    "missile": ["missile", "missiles", "rocket", "rockets", "misil", "misiles", "rakete", "raketen", "ракета", "ракеты"],
    "warship": ["warship", "warships", "frigate", "frigates", "destroyer", "destroyers", "corvette", "corvettes",
                "navire", "buque", "kriegsschiff"],
    "submarine": ["submarine", "submarines", "u-boat", "submarino", "sous-marin", "u-boot"],
    "aircraft": ["aircraft", "airplane", "airplanes", "jet", "jets", "fighter", "fighters", "avion", "avión", "flugzeug"],
    "drone": ["drone", "drones", "uav", "uavs", "uas", "dron", "drohne"],
    "tank": ["tank", "tanks", "mbt", "panzer", "tanque", "char"],
    "port": ["port", "ports", "harbor", "harbour", "harbors", "harbours", "naval-base", "puerto", "hafen"],
    "airbase": ["airbase", "airbases", "airfield", "airfields", "air-base", "aeródromo", "flugplatz"],
    "border": ["border", "borders", "frontier", "boundary", "frontera", "grenze", "frontière"],
    "satellite": ["satellite", "satellites", "imagery", "satélite", "satellit"],
    "document": ["document", "documents", "memo", "memos", "report", "reports", "filing", "documento", "dokument"],
    "convoy": ["convoy", "convoys", "column", "columns", "konvoi", "convoi"],
    "troop": ["troop", "troops", "soldier", "soldiers", "infantry", "personnel", "tropas", "truppen"],
    "ship": ["ship", "ships", "vessel", "vessels", "boat", "boats", "barco", "schiff"],
    "radar": ["radar", "radars"],
    "launcher": ["launcher", "launchers", "launch-site", "lanzador"],
    "exercise": ["exercise", "exercises", "drill", "drills", "manoeuvre", "manoeuvres", "maneuver", "maneuvers"],
    "fleet": ["fleet", "fleets", "flotilla", "flota", "flotte"],
}


def _fold(text: str) -> str:
    import unicodedata

    # strip combining accents for Latin scripts (é -> e); other scripts keep their letters
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


_SYN_INDEX: dict[str, str] = {}
for _concept, _words in SYNONYMS.items():
    for _w in _words:
        _SYN_INDEX[_fold(_w.lower())] = "~" + _concept


def content_tokens(text: str) -> set[str]:
    """Normalized matching tokens: folded lowercase words (any script, >=3 letters, no stopwords) plus
    shared concept tokens from the synonym table."""
    words = {w for w in _TOKEN_RE.findall(_fold((text or "").lower())) if w not in _STOP}
    concepts = {_SYN_INDEX[w] for w in words if w in _SYN_INDEX}
    return words | concepts


def extract_entities(text: str) -> list[str]:
    out: list[str] = []
    for m in _ENTITY_RE.finditer(text or ""):
        words = m.group(1).split()
        if m.start() == 0 and len(words) == 1:
            continue  # a single capitalised sentence-initial word is just grammar, not an entity
        while words and words[0] in _ENTITY_STOP:
            words = words[1:]
        ent = " ".join(words)
        if ent and len(ent) > 2 and ent not in out:
            out.append(ent)
    return out


def number_key(value, unit: str | None = None) -> str:
    """Canonical comparable form of a figure: '18', '18%', '12b'. Percent-ness and scale words must agree
    for a statistic graphic to be considered the same figure as the narrated one."""
    try:
        v = float(str(value).replace(",", ""))
    except ValueError:
        return str(value)
    txt = f"{v:g}"
    u = (unit or "").lower()
    if "%" in u or "percent" in u or "per cent" in u:
        return txt + "%"
    for word, suf in (("billion", "b"), ("million", "m"), ("thousand", "k")):
        if word in u:
            return txt + suf
    return txt


def extract_numbers(text: str) -> list[str]:
    nums = []
    for m in _NUMBER_RE.finditer(text or ""):
        n = m.group(1).replace(",", "")
        # skip bare 4-digit years without a unit (they are TIMELINE cues, not statistics)
        if not m.group(2) and re.fullmatch(r"(19|20)\d{2}", n):
            continue
        nums.append(number_key(n, m.group(2)))
    return nums


def classify_segment(text: str) -> dict:
    """Returns {primary_intent, intents, preferred_visuals, ai_allowed, entities, numbers, tokens}."""
    matched = [intent for intent in _PRIORITY if any(p.search(text or "") for p in _COMPILED[intent])]
    numbers = extract_numbers(text)
    if numbers and "STATISTIC" not in matched:
        matched.insert(0, "STATISTIC")  # a quoted figure is a statistic cue (spec §12)
    if "STATISTIC" in matched and not numbers:
        matched.remove("STATISTIC")  # "never invent statistics": a statistic shot needs an actual number
    weak = not matched  # no cue at all -> default intent; a short weak sentence never forces a cut
    if not matched:
        matched = ["EVENT_EXPLANATION"]
    primary = matched[0]
    preferred = INTENT_VISUALS[primary][0]
    # an illustration is acceptable only if EVERY matched intent permits it (an evidence/statistic/location
    # cue anywhere in the sentence forbids AI imagery for it, spec §8/§34)
    ai_allowed = all(INTENT_VISUALS[i][1] for i in matched)
    return {
        "primary_intent": primary,
        "intents": matched,
        "preferred_visuals": list(preferred),
        "ai_allowed": ai_allowed,
        "weak": weak,
        "entities": extract_entities(text),
        "numbers": numbers,
        "tokens": sorted(content_tokens(text)),
    }


def derive_visual_intents(narration_text: str) -> list[dict]:
    """Scene-level summary: [{intent, preferred_visual, fallback_visual, ai_allowed}] (spec §14 shape)."""
    from app.pipeline.scene_parser import split_sentences

    seen: list[str] = []
    for sentence in split_sentences(narration_text) or [narration_text or ""]:
        for intent in classify_segment(sentence)["intents"][:2]:
            if intent not in seen:
                seen.append(intent)
    out = []
    for intent in seen:
        prefs, ai_ok = INTENT_VISUALS[intent]
        out.append({"intent": intent, "preferred_visual": prefs[0], "fallback_visual": prefs[1] if len(prefs) > 1 else None,
                    "ai_allowed": ai_ok})
    return out
