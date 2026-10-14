"""OpenAI text-generation provider for Phase 3 script drafting.

Mirrors `acquisition/http_client.py`'s testable-seam pattern: a `client_factory`
lets tests inject an `httpx.MockTransport` (zero network, zero API credits) and
production uses a real `httpx.Client`. No `openai` SDK dependency is added --
Chat Completions is one bounded HTTPS POST, consistent with the project's
existing "small, auditable HTTP surface" convention (see `url_guard.py`).

Configuration is environment-based (`DEFENCE_STUDIO_OPENAI_API_KEY`); the key
is never logged, and prompt contents are not logged by this module. A live
smoke test exists separately and only runs when explicitly enabled -- the
automated test suite never spends real API credits.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import httpx

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_MAX_OUTPUT_TOKENS = 4096
# INR pricing table. Source/version is recorded on every usage record so a
# reported cost figure is reproducible; this is not a live pricing feed.
PRICING_SOURCE = "defence-studio.openai-pricing.manual.v1"
# USD per 1K tokens, converted at a fixed configured INR rate -- not live FX.
_PRICING_USD_PER_1K = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
}
_DEFAULT_USD_TO_INR = 88.0


class ProviderError(Exception):
    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    tokens_available: bool
    retries: int
    measured_text_cost_inr: float | None
    cost_is_measured: bool
    pricing_source: str
    currency: str = "INR"


def configured_api_key() -> str:
    return os.environ.get("DEFENCE_STUDIO_OPENAI_API_KEY", "")


def usd_to_inr_rate() -> float:
    try:
        return float(os.environ.get("DEFENCE_STUDIO_OPENAI_USD_TO_INR", _DEFAULT_USD_TO_INR))
    except (TypeError, ValueError):
        return _DEFAULT_USD_TO_INR


def estimate_cost_inr(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Pricing-table lookup. Returns None (never a guess) for an unknown model."""
    prices = _PRICING_USD_PER_1K.get(model)
    if prices is None:
        return None
    usd = (input_tokens / 1000) * prices["input"] + (output_tokens / 1000) * prices["output"]
    return round(usd * usd_to_inr_rate(), 4)


class OpenAiTextProvider:
    name = "openai"
    is_paid = True

    """Bounded, retrying Chat-Completions client behind a testable seam.

    Never called implicitly: the caller (ScriptService) only invokes this on an
    explicit, authorized generation action, never during ordinary page load,
    package inspection, job creation, or review.
    """

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None,
                timeout: float = _DEFAULT_TIMEOUT, max_retries: int = 2,
                client_factory=None):
        self.api_key = api_key if api_key is not None else configured_api_key()
        self.base_url = base_url or os.environ.get("DEFENCE_STUDIO_OPENAI_BASE_URL", _DEFAULT_BASE_URL)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._client_factory = client_factory or (lambda **kw: httpx.Client(**kw))

    def generate(self, *, model: str, system_prompt: str, user_prompt: str,
                max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS) -> ProviderResult:
        if not self.api_key:
            raise ProviderError("missing_api_key", "DEFENCE_STUDIO_OPENAI_API_KEY is not configured", retryable=False)
        if max_output_tokens <= 0 or max_output_tokens > 16000:
            raise ProviderError("invalid_bound", "max_output_tokens must be a bounded positive value", retryable=False)
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_output_tokens,
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        retries = 0
        last_error: ProviderError | None = None
        with self._client_factory(timeout=self.timeout) as client:
            while retries <= self.max_retries:
                try:
                    response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
                except httpx.TimeoutException as exc:
                    last_error = ProviderError("timeout", f"Provider request timed out after {self.timeout}s", retryable=True)
                    if retries >= self.max_retries:
                        raise last_error from exc
                    retries += 1
                    time.sleep(0)
                    continue
                except httpx.HTTPError as exc:
                    raise ProviderError("network_error", f"Network error contacting provider: {exc}", retryable=True) from exc
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = ProviderError(
                        "rate_limited" if response.status_code == 429 else "provider_error",
                        f"Provider returned HTTP {response.status_code}", retryable=True,
                    )
                    if retries >= self.max_retries:
                        raise last_error
                    retries += 1
                    time.sleep(0)
                    continue
                if response.status_code >= 400:
                    raise ProviderError("provider_rejected", f"Provider returned HTTP {response.status_code}: {response.text[:500]}",
                                        retryable=False)
                try:
                    parsed = response.json()
                    text = parsed["choices"][0]["message"]["content"]
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("empty completion")
                except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise ProviderError("malformed_response", f"Provider response did not match the expected shape: {exc}",
                                        retryable=False) from exc
                usage = parsed.get("usage") or {}
                input_tokens = usage.get("prompt_tokens")
                output_tokens = usage.get("completion_tokens")
                tokens_available = isinstance(input_tokens, int) and isinstance(output_tokens, int)
                measured_cost = (
                    estimate_cost_inr(model, input_tokens, output_tokens) if tokens_available else None
                )
                return ProviderResult(
                    text=text, input_tokens=input_tokens if tokens_available else None,
                    output_tokens=output_tokens if tokens_available else None,
                    tokens_available=tokens_available, retries=retries,
                    measured_text_cost_inr=measured_cost, cost_is_measured=measured_cost is not None,
                    pricing_source=PRICING_SOURCE,
                )
        raise last_error or ProviderError("unknown", "Provider call failed for an unknown reason", retryable=True)
