"""Offline, deterministic, extractive script provider (no network, no cost).

Explicitly NOT a language model: it only re-orders the approved claim texts it
is given into narration sections, each attributed (``ATTRIBUTED_CLAIM``) --
never ``VERIFIED_FACT`` -- because it performs no corroboration reasoning of
its own. It exists so the research-to-script gate can run and be tested
end to end without credentials. Usage is recorded as provider
``offline-extractive`` with cost 0 and ``cost_is_measured`` True (nothing was
spent). It must be selected explicitly with
``DEFENCE_STUDIO_SCRIPT_PROVIDER=offline``; the system never falls back to it
silently when an OpenAI key is missing.
"""
from __future__ import annotations

import json
import re

from defence_studio.providers.openai_text import PRICING_SOURCE, ProviderError, ProviderResult

_CLAIMS_RE = re.compile(r"for any material claim\):\n(\[.*?\n\])\n\nReturn a single JSON", re.DOTALL)


class OfflineExtractiveProvider:
    name = "offline-extractive"
    is_paid = False
    supports_response_schema = False

    def generate(self, *, model: str, system_prompt: str, user_prompt: str,
                 max_output_tokens: int = 4096) -> ProviderResult:
        match = _CLAIMS_RE.search(user_prompt)
        if not match:
            raise ProviderError("malformed_request", "Could not locate the approved evidence block", retryable=False)
        try:
            claims = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ProviderError("malformed_request", f"Evidence block is not valid JSON: {exc}", retryable=False) from exc
        if not claims:
            raise ProviderError("no_evidence", "No approved claims were supplied", retryable=False)
        topic_match = re.search(r"^Topic: (.*)$", user_prompt, re.MULTILINE)
        title = (topic_match.group(1).strip() if topic_match else "Untitled briefing")[:200] or "Untitled briefing"
        sections = []
        for index, claim in enumerate(claims, start=1):
            sections.append({
                "section_id": f"s{index:03d}",
                "heading": "",
                "narration": claim["text"],
                "requires_evidence": True,
                "statement_kind": "ATTRIBUTED_CLAIM",
                "evidence": {
                    "claim_ids": [claim["claim_id"]],
                    "source_ids": [claim["source_id"]] if claim.get("source_id") else [],
                    "qualification": "Reported by the cited source; not independently verified by this script.",
                },
                "presentation_note": "",
            })
        body = {
            "title": title,
            "opening_hook": "",
            "sections": sections,
            "closing": "This briefing restates only what the reviewed sources report.",
        }
        return ProviderResult(
            text=json.dumps(body, ensure_ascii=False), input_tokens=None, output_tokens=None,
            tokens_available=False, retries=0, measured_text_cost_inr=0.0, cost_is_measured=True,
            pricing_source=PRICING_SOURCE,
        )
