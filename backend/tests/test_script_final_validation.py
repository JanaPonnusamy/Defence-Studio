"""Regression: the live gpt-4o-mini draft (job 9f406f19) failed with UNSUPPORTED_NUMBER because
section-1 named "GSAT-7C" while citing only the claim about the signing date/attendee.
Claim texts below are the real extracted ones from that run. Offline and deterministic."""
import json

from defence_studio.services.script_prompt import user_prompt
from defence_studio.services.script_validation import validate_revision_body

SIGNING = ("The contracts were signed in the presence of Additional Secretary and Director General "
           "(Acquisition), Department of Defence Shri A Anbarasu in New Delhi on October 15, 2026.")
SATELLITE = ("The contract for GSAT-7C, valued at Rs 2,563 crore, was inked with New Space India Limited, Bengaluru.")


def package():
    def claim(cid, text):
        return {"claim_id": cid, "source_id": "src1", "claim_text": text, "source_published_at": "2026-10-15",
                "review": {"decision": "APPROVED_FOR_USE"}, "is_stale": False}
    return {"generated_from_timestamp": "2026-10-22T00:00:00+00:00",
            "sources": [{"source_id": "src1", "review": {"decision": "APPROVED_FOR_USE"}, "is_stale": False,
                         "declared_metadata": {"published_at": "2026-10-15", "publisher": "PIB"}}],
            "duplicate_groups": [], "claim_groups": [], "conflicts": [],
            "claims": [claim("c-sign", SIGNING), claim("c-sat", SATELLITE)],
            "eligible_claim_ids": ["c-sign", "c-sat"]}


def revision(claims, narration, *, closing="That is what the source reports.", source_ids=("src1",)):
    return {"package_sha256": "a" * 64, "title": "t", "closing": closing, "config": {"target_words": 40, "min_words": 1, "max_words": 5000}, "word_count": {"words": 40},
            "sections": [{"section_id": "s1", "narration": narration, "requires_evidence": True,
                          "statement_kind": "ATTRIBUTED_CLAIM",
                          "evidence": {"claim_ids": list(claims), "source_ids": list(source_ids), "qualification": "q"}}]}


def codes(rev):
    return {i["code"]: i["severity"] for i in validate_revision_body(rev, package())["issues"]}


def test_designation_in_narration_must_come_from_the_cited_claim():
    narration = "The GSAT-7C contract was signed in New Delhi on October 15, 2026."
    assert codes(revision(["c-sign"], narration))["UNSUPPORTED_NUMBER"] == "ERROR"      # the live failure
    assert "UNSUPPORTED_NUMBER" not in codes(revision(["c-sign", "c-sat"], narration))  # cite the claim that names it
    generic = "The satellite contract was signed in New Delhi on October 15, 2026."
    assert "UNSUPPORTED_NUMBER" not in codes(revision(["c-sign"], generic))              # or stay generic


def test_invented_numbers_and_dates_are_rejected():
    assert codes(revision(["c-sat"], "The contract is worth Rs 2,999 crore."))["UNSUPPORTED_NUMBER"] == "ERROR"
    assert codes(revision(["c-sign"], "It was signed on October 16, 2026."))["UNSUPPORTED_NUMBER"] == "ERROR"


def test_empty_closing_and_invented_source_id_fail():
    assert codes(revision(["c-sign"], "Signed in New Delhi.", closing="  "))["MISSING_CLOSING"] == "ERROR"
    assert validate_revision_body(revision(["c-sign"], "Signed in New Delhi.", source_ids=("informational",)),
                                  package())["passed"] is False


def test_prompt_tells_the_model_exactly_which_digits_each_claim_allows():
    claims = [{"claim_id": "c-sat", "claim_text": SATELLITE, "source_id": "src1"}]
    text = user_prompt(topic="t", language="en", audience="a", tone="t", script_format="f", target_words=120,
                       terminology={}, eligible_claims=claims, corroboration={"c-sat": 1})
    payload = json.loads(text.split("ONLY facts you may cite; do not use outside knowledge for any material claim):\n")[1]
                         .split("\n\nReturn a single JSON")[0])
    assert payload[0]["digits_allowed"] and all(isinstance(d, str) for d in payload[0]["digits_allowed"])
    assert "7" in payload[0]["digits_allowed"] and "2563" in "".join(payload[0]["digits_allowed"]).replace(",", "")
    assert "generically" in text   # the designation-must-be-cited rule is present
