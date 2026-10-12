"""
OPTIONAL real AI image provider: OpenAI Images API (POST /v1/images/generations).
generation_kind = AI_GENERATED. Billable. Disabled in practice until a key is set.

Official docs used (retrieved 2026-10-12; see docs/PROVIDER_SETUP.md):
  * https://developers.openai.com/api/docs/guides/image-generation
  * https://developers.openai.com/api/docs/models/gpt-image-2
  * https://developers.openai.com/api/docs/pricing

Behaviour:
  * Key comes ONLY from the environment / module .env (OPENAI_API_KEY or
    VIDEO_MODULE_OPENAI_API_KEY). It is held as a SecretStr, sent only in the
    Authorization header, never logged, never returned, and scrubbed from any
    provider error text before that text is stored or raised.
  * No key -> PROVIDER_NOT_CONFIGURED, and NO network request is attempted.
  * `config_state()` never touches the network. `check_connectivity()` makes one
    non-generating, non-billable GET /v1/models/{model} call, and only when a key
    is configured.
  * Output size is always one of the documented preset sizes (1024x1024,
    1536x1024, 1024x1536) chosen from the requested aspect, so the documented
    per-image price table applies to the budget estimate. The real decoded
    dimensions are what get recorded.
  * Recorded cost = documented token rates x `usage` returned by the API; if usage
    is missing, cost status is UNKNOWN (never guessed).
"""
from __future__ import annotations

import base64
import binascii
import io
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from app.generation.base import (
    COST_KNOWN, COST_UNKNOWN, GEN_AI, STATE_AVAILABLE, STATE_FAILED, STATE_NOT_CONFIGURED, STATE_UNAVAILABLE,
    AIImageProvider, CostInfo, ImageGenerationRequest, ImageGenerationResult, ProviderError, parse_resolution,
)
from app.generation.openai_pricing import (
    PRICING_RETRIEVED_ON, PRICING_SOURCE_PER_IMAGE, cost_from_usage, per_image_estimate,
)

QUALITY_MAP = {"draft": "low", "standard": "medium", "high": "high"}
PRESET_SIZES = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}
_MAX_IMAGE_BYTES = 40 * 1024 * 1024


def _settings():
    from app.config import get_settings

    return get_settings()


def preset_size_for(resolution: str) -> str:
    w, h = parse_resolution(resolution)
    ratio = w / h
    if ratio > 1.05:
        return PRESET_SIZES["landscape"]
    if ratio < 0.95:
        return PRESET_SIZES["portrait"]
    return PRESET_SIZES["square"]


def _png_has_c2pa(data: bytes) -> bool | None:
    """True if a PNG carries a C2PA manifest chunk ('caBX'); None if not a PNG. Detection only."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        if ctype == b"caBX":
            return True
        if ctype == b"IEND":
            break
        pos += 12 + length
    return False


class OpenAIImagesProvider(AIImageProvider):
    name = "openai-images"
    is_mock = False
    generation_kind = GEN_AI
    requires_credentials = True
    billable = True
    description = ("OpenAI Images API (real generative AI model). Optional: requires OPENAI_API_KEY. Billable per "
                   "OpenAI pricing. Output is AI-generated ILLUSTRATION, never evidence.")
    # tests inject an httpx.MockTransport here; None = real network transport
    _transport: httpx.BaseTransport | None = None

    # ---------------------------------------------------------------- config
    @property
    def model(self) -> str:  # type: ignore[override]
        return _settings().openai_image_model

    def _key(self) -> str | None:
        k = _settings().openai_api_key
        val = k.get_secret_value().strip() if k is not None else ""
        return val or None

    def config_state(self) -> str:
        return STATE_AVAILABLE if self._key() else STATE_NOT_CONFIGURED

    def _redact(self, text: str) -> str:
        from app.logging_conf import redact

        key = self._key()
        text = str(text or "")
        if key:
            text = text.replace(key, "[REDACTED]")
            if len(key) > 12:  # providers sometimes echo a partially masked key
                text = text.replace(key[-6:], "[REDACTED]").replace(key[:8], "[REDACTED]")
        return redact(text)[:500]

    def _client(self) -> httpx.Client:
        s = _settings()
        return httpx.Client(base_url=s.openai_base_url.rstrip("/"), timeout=httpx.Timeout(s.openai_timeout_s),
                            transport=self._transport, follow_redirects=False)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key()}", "Content-Type": "application/json"}

    # ---------------------------------------------------------------- estimates
    def estimate_cost(self, request: ImageGenerationRequest) -> float | None:
        try:
            size = preset_size_for(request.resolution)
        except ValueError:
            return None
        return per_image_estimate(self.model, QUALITY_MAP.get(request.quality, "medium"), size)

    def estimate_basis(self, request: ImageGenerationRequest) -> str:
        if self.estimate_cost(request) is None:
            return f"no documented per-image price for model {self.model!r}; estimate unknown"
        return (f"documented per-image output price for {self.model} quality={QUALITY_MAP.get(request.quality)} "
                f"size={preset_size_for(request.resolution)} ({PRICING_SOURCE_PER_IMAGE}, retrieved "
                f"{PRICING_RETRIEVED_ON}); excludes prompt text-input tokens")

    # ---------------------------------------------------------------- connectivity (non-generating)
    def check_connectivity(self) -> dict:
        if not self._key():
            return {"state": STATE_NOT_CONFIGURED, "probed": False,
                    "detail": "OPENAI_API_KEY is not set; no request was made."}
        try:
            with self._client() as c:
                r = c.get(f"/models/{self.model}", headers=self._headers())
        except httpx.TimeoutException:
            return {"state": STATE_UNAVAILABLE, "probed": True, "detail": "timeout contacting provider"}
        except httpx.TransportError as exc:
            return {"state": STATE_UNAVAILABLE, "probed": True, "detail": f"network error: {type(exc).__name__}"}
        rid = r.headers.get("x-request-id")
        if r.status_code == 200:
            return {"state": STATE_AVAILABLE, "probed": True, "detail": f"model {self.model} visible to this key",
                    "provider_request_id": rid}
        if r.status_code in (401, 403):
            return {"state": STATE_FAILED, "probed": True, "detail": f"authentication/permission failed (HTTP {r.status_code})",
                    "provider_request_id": rid}
        if r.status_code == 404:
            return {"state": STATE_FAILED, "probed": True, "detail": f"model {self.model} not available to this key",
                    "provider_request_id": rid}
        if r.status_code == 429 or r.status_code >= 500:
            return {"state": STATE_UNAVAILABLE, "probed": True, "detail": f"provider returned HTTP {r.status_code}",
                    "provider_request_id": rid}
        return {"state": STATE_FAILED, "probed": True, "detail": f"unexpected HTTP {r.status_code}", "provider_request_id": rid}

    # ---------------------------------------------------------------- generation
    def _error_from_response(self, r: httpx.Response) -> ProviderError:
        rid = r.headers.get("x-request-id")
        try:
            err = (r.json() or {}).get("error") or {}
        except ValueError:
            err = {}
        code = str(err.get("code") or err.get("type") or "")
        msg = self._redact(err.get("message") or r.text[:300])
        detail = f"OpenAI HTTP {r.status_code}{f' ({code})' if code else ''}: {msg}"
        if r.status_code == 401 or r.status_code == 403:
            return ProviderError("PROVIDER_AUTH_FAILED", detail, provider_request_id=rid)
        if r.status_code == 429:
            if code == "insufficient_quota" or "quota" in msg.lower() or "billing" in msg.lower():
                return ProviderError("PROVIDER_QUOTA", detail, provider_request_id=rid)
            # an explicit 429 rate-limit rejection means the request was not processed
            return ProviderError("PROVIDER_RATE_LIMITED", detail, provider_request_id=rid, not_processed=True)
        if r.status_code >= 500:
            return ProviderError("PROVIDER_UNAVAILABLE", detail, provider_request_id=rid)
        if code in ("moderation_blocked", "content_policy_violation") or err.get("type") == "image_generation_user_error":
            return ProviderError("PROVIDER_CONTENT_REJECTED", detail, provider_request_id=rid)
        return ProviderError("GENERATION_FAILED", detail, provider_request_id=rid)

    def generate(self, request: ImageGenerationRequest, out_dir: Path) -> ImageGenerationResult:
        key = self._key()
        if not key:
            raise ProviderError("PROVIDER_NOT_CONFIGURED",
                                "openai-images is not configured (OPENAI_API_KEY not set); no request was made.")
        start = time.monotonic()
        try:
            size = preset_size_for(request.resolution)
        except ValueError as exc:
            raise ProviderError("GENERATION_FAILED", f"Invalid resolution: {exc}") from exc
        quality = QUALITY_MAP.get(request.quality, "medium")
        model = self.model
        body = {"model": model, "prompt": request.prompt, "n": 1, "size": size, "quality": quality,
                "output_format": "png"}
        try:
            with self._client() as c:
                r = c.post("/images/generations", json=body, headers=self._headers())
        # ConnectTimeout/PoolTimeout subclass TimeoutException, so connect-phase errors must be caught first.
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE",
                                f"Could not connect to OpenAI ({type(exc).__name__}); the request was not sent.",
                                not_processed=True) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "PROVIDER_TIMEOUT",
                f"Timed out ({type(exc).__name__}) after the request was sent. OpenAI may have processed and billed it; "
                "outcome UNCERTAIN. Not retried automatically -- check the OpenAI usage dashboard before resubmitting.",
                outcome_uncertain=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                "PROVIDER_UNAVAILABLE",
                f"Connection to OpenAI failed after the request was sent ({type(exc).__name__}); outcome UNCERTAIN. "
                "Not retried automatically.", outcome_uncertain=True) from exc
        if r.status_code != 200:
            raise self._error_from_response(r)
        rid = r.headers.get("x-request-id")
        try:
            payload = r.json()
            item = payload["data"][0]
            b64 = item.get("b64_json")
            if not isinstance(b64, str) or not b64:
                raise ProviderError("PROVIDER_RESPONSE_INVALID",
                                    "Response has no b64_json image data (URL responses are not fetched).",
                                    provider_request_id=rid)
            if len(b64) > _MAX_IMAGE_BYTES * 4 // 3 + 16:
                raise ProviderError("PROVIDER_RESPONSE_INVALID", "Returned image exceeds size limit.", provider_request_id=rid)
            data = base64.b64decode(b64, validate=True)
        except ProviderError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, binascii.Error) as exc:
            raise ProviderError("PROVIDER_RESPONSE_INVALID", f"Malformed provider response: {type(exc).__name__}",
                                provider_request_id=rid) from exc
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                width, height, fmt = im.width, im.height, im.format
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ProviderError("PROVIDER_RESPONSE_INVALID", "Returned image data is not a decodable image.",
                                provider_request_id=rid) from exc
        ext = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(fmt or "")
        if ext is None:
            raise ProviderError("PROVIDER_RESPONSE_INVALID", f"Unexpected image format {fmt}.", provider_request_id=rid)

        out_dir.mkdir(parents=True, exist_ok=True)
        request_id = str(uuid.uuid4())
        file_path = out_dir / f"{request_id}.openai.{ext}"
        file_path.write_bytes(data)

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        amount, basis = cost_from_usage(model, usage)
        cost = CostInfo(status=COST_KNOWN if amount is not None else COST_UNKNOWN, amount=amount, basis=basis,
                        usage=usage)
        created = payload.get("created")
        created_iso = (datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
                       if isinstance(created, (int, float)) else None)
        meta = {
            "provider_api": "openai.images.generations",
            "requested_size": size, "requested_quality": quality, "requested_resolution": request.resolution,
            "provider_created": created_iso,
            "response_size": payload.get("size"), "response_quality": payload.get("quality"),
            "response_output_format": payload.get("output_format"), "response_background": payload.get("background"),
            "revised_prompt": item.get("revised_prompt"),
            "usage": usage,
            "c2pa_manifest_present": _png_has_c2pa(data),
        }
        return ImageGenerationResult(
            provider=self.name, is_mock=False, model=model, request_id=request_id, file_path=file_path,
            width=width, height=height, generation_time_seconds=round(time.monotonic() - start, 3),
            estimated_cost=amount, metadata={k: v for k, v in meta.items() if v is not None},
            generation_kind=GEN_AI, cost=cost, provider_request_id=rid,
            mime_type={"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[ext],
        )
