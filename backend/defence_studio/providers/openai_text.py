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

import re

import httpx

_KEY_LIKE = re.compile(r"sk-[A-Za-z0-9_\-*.]{4,}|Bearer\s+[A-Za-z0-9_\-.]{8,}")


def scrub_secrets(text: str, *secrets_: str) -> str:
    """Remove the configured key and any key-shaped token from provider text."""
    for s in secrets_:
        if s:
            text = text.replace(s, "***")
    return _KEY_LIKE.sub("***", text)


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
    "gpt-4.1": {"input": 0.002, "output": 0.008},
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


# Server-environment key names, in precedence order. The readiness check
# (verification/providers.py), the credential status view and the provider all
# resolve through these so they can never disagree about "configured".
API_KEY_ENV_NAMES = ("DEFENCE_STUDIO_OPENAI_API_KEY", "OPENAI_API_KEY")


def api_key_env_source() -> str | None:
    """Name (never the value) of the environment variable that supplies the key."""
    for name in API_KEY_ENV_NAMES:
        if (os.environ.get(name) or "").strip():
            return name
    return None


def configured_api_key() -> str:
    name = api_key_env_source()
    return os.environ[name].strip() if name else ""


def _is_reasoning_model(model: str) -> bool:
    """o-series and gpt-5 models reject `max_tokens` and a non-default temperature."""
    return bool(re.match(r"^(o\d|gpt-5)", (model or "").strip().lower()))


_MAX_OUTPUT_TOKENS_CEILING = 32000
_JSON_OVERHEAD_TOKENS = 2000       # keys, evidence maps, qualifications around the narration
_REASONING_ALLOWANCE_TOKENS = 8000  # reasoning models spend completion tokens before visible output


def output_token_limit(model: str, language: str, max_words: int) -> int:
    """Completion-token limit for a script of up to `max_words` spoken words.

    Conservative tokens-per-word: ~2.5 for English, ~8 for Tamil and any other
    language. Reasoning models (o-series, gpt-5) count hidden reasoning tokens
    against the same limit, so they get an allowance. Capped at a fixed ceiling;
    a truncated completion is a provider failure, never a successful script."""
    per_word = 2.5 if (language or "").lower().startswith("en") else 8.0
    limit = int(max_words * per_word) + _JSON_OVERHEAD_TOKENS
    if _is_reasoning_model(model):
        limit += _REASONING_ALLOWANCE_TOKENS
    return min(limit, _MAX_OUTPUT_TOKENS_CEILING)


def usd_to_inr_rate() -> float:
    try:
        return float(os.environ.get("DEFENCE_STUDIO_OPENAI_USD_TO_INR", _DEFAULT_USD_TO_INR))
    except (TypeError, ValueError):
        return _DEFAULT_USD_TO_INR


def supports_strict_json_schema(model: str) -> bool:
    """True only for Chat Completions models known to accept strict json_schema.

    Anything else stays on parse-and-validate. This client does not send a
    second paid request to discover support.
    """
    name = (model or "").strip().lower()
    if not name:
        return False
    for prefix in ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1", "gpt-4o"):
        if name == prefix or name.startswith(prefix + "-"):
            return True
    return False


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
    supports_response_schema = True

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
        # Diagnostic capture of the most recent call: the exact JSON body sent
        # and the raw response. Never contains the Authorization header.
        self.last_exchange: dict | None = None

    def generate(self, *, model: str, system_prompt: str, user_prompt: str,
                max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
                response_schema: dict | None = None,
                response_schema_name: str = "defence_studio_script_draft_v1") -> ProviderResult:
        if not self.api_key:
            raise ProviderError("missing_api_key", "DEFENCE_STUDIO_OPENAI_API_KEY is not configured", retryable=False)
        if max_output_tokens <= 0 or max_output_tokens > _MAX_OUTPUT_TOKENS_CEILING:
            raise ProviderError("invalid_bound", "max_output_tokens must be a bounded positive value", retryable=False)
        reasoning = _is_reasoning_model(model)
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # `max_tokens` is deprecated and rejected by reasoning models; the official
        # endpoint takes `max_completion_tokens`. A custom OpenAI-compatible base URL
        # keeps the widely supported legacy name.
        official = self.base_url.rstrip("/") == _DEFAULT_BASE_URL
        body["max_completion_tokens" if (official or reasoning) else "max_tokens"] = max_output_tokens
        if not reasoning:
            body["temperature"] = 0.2
        constrained = bool(response_schema) and supports_strict_json_schema(model)
        if constrained:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            }
        elif response_schema is None:
            # JSON mode only. It is not a strict schema and is not reported as one.
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        endpoint = f"{self.base_url}/chat/completions"
        exchange = self.last_exchange = {
            "endpoint": endpoint, "method": "POST", "timeout_s": self.timeout, "max_retries": self.max_retries,
            "request_body": body, "attempts": [], "response": None,
            "output_constraint": {
                "mode": "json_schema" if constrained else "parse_and_validate",
                "schema_name": response_schema_name if response_schema else None,
                "strict": constrained,
                "parse": None,
            },
        }
        started = time.monotonic()

        def _note(response, attempt: int) -> None:
            text = scrub_secrets(response.text or "", self.api_key)
            exchange["attempts"].append({
                "attempt": attempt + 1, "http_status": response.status_code,
                "duration_ms": int((time.monotonic() - started) * 1000)})
            exchange["response"] = {
                "http_status": response.status_code, "body": text[:200000],
                "truncated": len(text) > 200000,
                "provider_request_id": response.headers.get("x-request-id"),
                "duration_ms": int((time.monotonic() - started) * 1000)}

        retries = 0
        last_error: ProviderError | None = None
        with self._client_factory(timeout=self.timeout) as client:
            while retries <= self.max_retries:
                try:
                    response = client.post(endpoint, headers=headers, json=body)
                except httpx.TimeoutException as exc:
                    exchange["attempts"].append({"attempt": retries + 1, "error": "timeout"})
                    last_error = ProviderError("timeout", f"Provider request timed out after {self.timeout}s", retryable=True)
                    if retries >= self.max_retries:
                        raise last_error from exc
                    retries += 1
                    time.sleep(0)
                    continue
                except httpx.HTTPError as exc:
                    exchange["attempts"].append({"attempt": retries + 1, "error": "network_error"})
                    raise ProviderError("network_error", f"Network error contacting provider: {scrub_secrets(str(exc), self.api_key)}", retryable=True) from exc
                _note(response, retries)
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
                    raise ProviderError("provider_rejected",
                                        f"Provider returned HTTP {response.status_code}: "
                                        f"{scrub_secrets(response.text[:500], self.api_key)}",
                                        retryable=False)
                try:
                    parsed = response.json()
                    choice = parsed["choices"][0]
                    message = choice["message"]
                    if not isinstance(message, dict):
                        raise ValueError("completion message was not an object")
                    finish = choice.get("finish_reason")
                    if finish == "length":
                        raise ProviderError("truncated_output", "Provider output was truncated at the token limit", retryable=False)
                    if finish == "content_filter":
                        raise ProviderError("content_filtered", "Provider content filter rejected the completion", retryable=False)
                    refusal = message.get("refusal")
                    if isinstance(refusal, str) and refusal.strip():
                        raise ProviderError(
                            "refusal",
                            "Provider refused the request: " + scrub_secrets(refusal[:500], self.api_key),
                            retryable=False,
                        )
                    text = message.get("content")
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("empty completion")
                except ProviderError:
                    raise
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
