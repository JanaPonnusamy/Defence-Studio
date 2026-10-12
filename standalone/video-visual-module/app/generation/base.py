"""
Provider-neutral image generation adapter contract (spec §22/§23), contract v2.

Core pipeline code (app/pipeline/*) never calls a provider-specific API -- it
only talks to this interface through app/generation/service.py. Concrete
providers and their honest classification (`generation_kind`):

  * openai_images.py        -> AI_GENERATED  real AI model (OpenAI Images API).
                               Optional; needs OPENAI_API_KEY. Billable.
  * placeholder_procedural  -> PLACEHOLDER   local deterministic procedural
                               renderer. NOT AI, NOT evidence. No cost.
                               (formerly named "local-procedural"; that name
                               is still accepted as an alias)
  * mock_local.py           -> TEST_FIXTURE  test stand-in for an AI provider.

`generation_kind` is carried into GenerationRecord, CostRecord, Asset and every
API response, so a placeholder or fixture can never be counted, reported or
selected as a real AI image (and no generated image is ever evidence).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ADAPTER_CONTRACT_VERSION = "2.0"

# kept for callers that scale estimates by quality (no provider uses invented rates any more)
QUALITY_MULTIPLIER = {"draft": 0.5, "standard": 1.0, "high": 2.0}
ALLOWED_QUALITIES = set(QUALITY_MULTIPLIER)
ALLOWED_ASPECTS = {"16:9", "9:16", "1:1", "4:3", "3:2", "21:9"}
ALLOWED_PURPOSES = {"HOOK", "HERO", "ILLUSTRATION", "ATMOSPHERE", "TRANSITION", "CONCEPT", "CONCLUSION"}

# ---- classification of generated output
GEN_AI = "AI_GENERATED"          # produced by a real generative AI model
GEN_PLACEHOLDER = "PLACEHOLDER"  # procedural/placeholder art, not AI
GEN_FIXTURE = "TEST_FIXTURE"     # mock/test output
GEN_NONE = "NOT_GENERATED"       # imported or data-rendered asset
GENERATION_KINDS = {GEN_AI, GEN_PLACEHOLDER, GEN_FIXTURE, GEN_NONE}

# ---- provider / generation states
STATE_NOT_CONFIGURED = "NOT_CONFIGURED"  # credentials/config missing; no request was attempted
STATE_UNAVAILABLE = "UNAVAILABLE"        # configured but unreachable (network, 5xx, rate limit after retries)
STATE_FAILED = "FAILED"                  # provider answered but the generation failed (auth, quota, bad output...)
STATE_SUCCEEDED = "SUCCEEDED"
STATE_AVAILABLE = "AVAILABLE"            # (config check only) ready to use; for billable providers not probed unless asked
STATE_UNCERTAIN = "UNCERTAIN"            # request may have been processed/billed but no result was received (e.g. timeout)
PROVIDER_STATES = {STATE_NOT_CONFIGURED, STATE_UNAVAILABLE, STATE_FAILED, STATE_SUCCEEDED, STATE_AVAILABLE,
                   STATE_UNCERTAIN}

# ---- cost status
COST_KNOWN = "KNOWN"                    # computed from documented pricing x actual usage in the response
COST_UNKNOWN = "UNKNOWN"                # billable but the actual cost could not be determined
COST_NOT_APPLICABLE = "NOT_APPLICABLE"  # no billable provider involved (placeholder / fixture)
COST_STATUSES = {COST_KNOWN, COST_UNKNOWN, COST_NOT_APPLICABLE}

PROVIDER_ERROR_CODES = {
    "GENERATION_FAILED", "PROVIDER_TIMEOUT", "PROVIDER_QUOTA", "PROVIDER_NOT_CONFIGURED", "PROVIDER_UNAVAILABLE",
    "PROVIDER_AUTH_FAILED", "PROVIDER_RATE_LIMITED", "PROVIDER_RESPONSE_INVALID", "PROVIDER_CONTENT_REJECTED",
}
# transient codes are retried by the generation service (bounded) -- non-billable providers only
TRANSIENT_PROVIDER_CODES = {"PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMITED", "PROVIDER_UNAVAILABLE"}
_STATE_FOR_CODE = {
    "PROVIDER_NOT_CONFIGURED": STATE_NOT_CONFIGURED,
    "PROVIDER_UNAVAILABLE": STATE_UNAVAILABLE,
    "PROVIDER_TIMEOUT": STATE_UNAVAILABLE,
    "PROVIDER_RATE_LIMITED": STATE_UNAVAILABLE,
}


def state_for_code(code: str) -> str:
    return _STATE_FOR_CODE.get(code, STATE_FAILED)


def parse_resolution(resolution: str) -> tuple[int, int]:
    """'1280x720' -> (1280, 720). Raises ValueError on bad input."""
    w, h = str(resolution).lower().split("x")
    w_i, h_i = int(w), int(h)
    if not (64 <= w_i <= 4096 and 64 <= h_i <= 4096):
        raise ValueError("resolution sides must be within 64..4096")
    return w_i, h_i


@dataclass
class ImageGenerationRequest:
    prompt: str
    aspect_ratio: str = "16:9"
    resolution: str = "1280x720"
    quality: str = "standard"
    purpose: str | None = None
    scene_id: str | None = None
    reference_assets: list[str] = field(default_factory=list)


@dataclass
class CostInfo:
    status: str                      # KNOWN | UNKNOWN | NOT_APPLICABLE
    amount: float | None = None      # only when status == KNOWN
    currency: str = "USD"
    basis: str = ""                  # how it was derived (pricing source + usage), or why unknown
    usage: dict | None = None        # raw usage block returned by the provider, if any


@dataclass
class ImageGenerationResult:
    provider: str
    is_mock: bool
    model: str
    request_id: str
    file_path: Path
    width: int
    height: int
    generation_time_seconds: float
    # legacy field: the KNOWN cost amount or None. Never an invented number.
    estimated_cost: float | None
    currency: str = "USD"
    metadata: dict = field(default_factory=dict)
    generation_kind: str = GEN_FIXTURE
    cost: CostInfo | None = None
    provider_request_id: str | None = None  # id returned by the remote provider (e.g. x-request-id header)
    mime_type: str = "image/png"


class AIImageProvider:
    """Abstract base. Every concrete provider sets `name`, `model`, `is_mock`, `generation_kind`."""

    name: str = "base"
    model: str = "none"
    is_mock: bool = True
    generation_kind: str = GEN_FIXTURE
    requires_credentials: bool = False
    billable: bool = False
    aliases: tuple[str, ...] = ()
    description: str = ""

    def estimate_cost(self, request: ImageGenerationRequest) -> float | None:
        """Pre-call estimate from DOCUMENTED pricing, or None if no documented price applies.
        Non-billable providers return None (cost is not applicable)."""
        return None

    def estimate_basis(self, request: ImageGenerationRequest) -> str:
        return "not applicable (no billable provider)" if not self.billable else "no documented price for this request"

    def config_state(self) -> str:
        """Configuration-only state. MUST NOT make network calls or generate anything."""
        return STATE_AVAILABLE

    def check_connectivity(self) -> dict:
        """Optional cheap, non-generating connectivity probe. Default: nothing to probe."""
        return {"state": self.config_state(), "probed": False, "detail": "local provider; nothing to probe"}

    def generate(self, request: ImageGenerationRequest, out_dir: Path) -> ImageGenerationResult:
        raise NotImplementedError

    def label(self) -> str:
        if self.generation_kind == GEN_FIXTURE or self.is_mock:
            return "MOCK / TEST FIXTURE (not a real generation)"
        if self.generation_kind == GEN_PLACEHOLDER:
            return "PLACEHOLDER (procedural, not AI, not evidence)"
        return "REAL AI MODEL (illustrative only, not evidence)"

    def describe(self) -> dict:
        state = self.config_state()
        return {
            "name": self.name,
            "model": self.model,
            "is_mock": self.is_mock,
            "generation_kind": self.generation_kind,
            "is_real_ai": self.generation_kind == GEN_AI,
            "requires_credentials": self.requires_credentials,
            "billable": self.billable,
            "state": state,
            "aliases": list(self.aliases),
            "label": self.label(),
            "description": self.description,
            "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
        }


class ProviderError(Exception):
    """Raised by a provider. `code` is one of PROVIDER_ERROR_CODES (unknown codes become
    GENERATION_FAILED). Codes in TRANSIENT_PROVIDER_CODES are retried by the service for
    NON-billable providers only; billable providers are retried only when `not_processed`.
    Messages must never contain credentials (providers redact before raising).

    not_processed: the provider demonstrably did not process the request (connection never
      established, or an explicit 429 rate-limit rejection) -- the only case where resending a
      billable request cannot double-bill.
    outcome_uncertain: the request may have reached and been processed (and billed) by the
      provider, e.g. a read timeout after sending. Never retried, never covered by fallback."""

    def __init__(self, code: str, message: str, *, provider_request_id: str | None = None,
                 not_processed: bool = False, outcome_uncertain: bool = False):
        self.code = code if code in PROVIDER_ERROR_CODES else "GENERATION_FAILED"
        self.message = message
        self.provider_request_id = provider_request_id
        self.not_processed = not_processed and not outcome_uncertain
        self.outcome_uncertain = outcome_uncertain
        self.state = STATE_UNCERTAIN if outcome_uncertain else state_for_code(self.code)
        super().__init__(message)
