"""
DOCUMENTED OpenAI image pricing used by the openai-images adapter.

Source (official, retrieved 2026-10-12 -- see docs/PROVIDER_SETUP.md):
  * token rates:      https://developers.openai.com/api/docs/pricing  ("Image generation", Standard, per 1M tokens)
  * per-image prices: https://developers.openai.com/api/docs/guides/image-generation  (cost table for the
                      three preset sizes; GPT Image 2 group, plus legacy table for 1.5 / 1 / 1-mini)

Rules (Phase 2, no invented numbers):
  * The RECORDED cost of a generation is computed ONLY from these documented token
    rates multiplied by the `usage` token counts actually returned in the API
    response. If usage is missing/incomplete or the model is not in this table, the
    cost is recorded as UNKNOWN.
  * The per-image table is used ONLY for the pre-call budget ESTIMATE (it covers image
    output only; the few prompt text-input tokens are not included and are noted in the basis).
Prices change: re-verify against the source pages and update PRICING_RETRIEVED_ON.
"""
from __future__ import annotations

PRICING_RETRIEVED_ON = "2026-10-12"
PRICING_SOURCE_TOKENS = "https://developers.openai.com/api/docs/pricing"
PRICING_SOURCE_PER_IMAGE = "https://developers.openai.com/api/docs/guides/image-generation"

# USD per 1M tokens (Standard tier). text_out None = not listed for that model.
TOKEN_RATES_PER_1M: dict[str, dict[str, float | None]] = {
    "gpt-image-2":      {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00, "text_out": None},
    "gpt-image-1.5":    {"text_in": 5.00, "image_in": 8.00, "image_out": 32.00, "text_out": 10.00},
    "gpt-image-1":      {"text_in": 5.00, "image_in": 10.00, "image_out": 40.00, "text_out": None},
    "gpt-image-1-mini": {"text_in": 2.00, "image_in": 2.50, "image_out": 8.00, "text_out": None},
}

# USD per generated image, by OpenAI quality and preset size (image output only).
PER_IMAGE_USD: dict[str, dict[str, dict[str, float]]] = {
    "gpt-image-2": {
        "low":    {"1024x1024": 0.006, "1024x1536": 0.005, "1536x1024": 0.005},
        "medium": {"1024x1024": 0.053, "1024x1536": 0.041, "1536x1024": 0.041},
        "high":   {"1024x1024": 0.211, "1024x1536": 0.165, "1536x1024": 0.165},
    },
    "gpt-image-1.5": {
        "low":    {"1024x1024": 0.009, "1024x1536": 0.013, "1536x1024": 0.013},
        "medium": {"1024x1024": 0.034, "1024x1536": 0.05, "1536x1024": 0.05},
        "high":   {"1024x1024": 0.133, "1024x1536": 0.2, "1536x1024": 0.2},
    },
    "gpt-image-1": {
        "low":    {"1024x1024": 0.011, "1024x1536": 0.016, "1536x1024": 0.016},
        "medium": {"1024x1024": 0.042, "1024x1536": 0.063, "1536x1024": 0.063},
        "high":   {"1024x1024": 0.167, "1024x1536": 0.25, "1536x1024": 0.25},
    },
    "gpt-image-1-mini": {
        "low":    {"1024x1024": 0.005, "1024x1536": 0.006, "1536x1024": 0.006},
        "medium": {"1024x1024": 0.011, "1024x1536": 0.015, "1536x1024": 0.015},
        "high":   {"1024x1024": 0.036, "1024x1536": 0.052, "1536x1024": 0.052},
    },
}


def _base_model(model: str) -> str:
    """'gpt-image-2-2026-04-21' -> 'gpt-image-2' (dated snapshots share the family price)."""
    for m in sorted(TOKEN_RATES_PER_1M, key=len, reverse=True):
        if model == m or model.startswith(m + "-20"):
            return m
    return model


def per_image_estimate(model: str, quality: str, size: str) -> float | None:
    return PER_IMAGE_USD.get(_base_model(model), {}).get(quality, {}).get(size)


def cost_from_usage(model: str, usage: dict | None) -> tuple[float | None, str]:
    """Return (amount_usd, basis). amount is None when it cannot be computed honestly."""
    rates = TOKEN_RATES_PER_1M.get(_base_model(model))
    if rates is None:
        return None, f"model {model!r} has no documented token price in this module's table"
    if not isinstance(usage, dict):
        return None, "provider response contained no usage data"
    try:
        in_total = int(usage.get("input_tokens", 0) or 0)
        out_total = int(usage["output_tokens"])
        in_d = usage.get("input_tokens_details") or {}
        out_d = usage.get("output_tokens_details") or None
        text_in = int(in_d.get("text_tokens", in_total - int(in_d.get("image_tokens", 0) or 0)) or 0)
        image_in = int(in_d.get("image_tokens", 0) or 0)
        if out_d is not None:
            image_out = int(out_d.get("image_tokens", 0) or 0)
            text_out = int(out_d.get("text_tokens", 0) or 0)
        elif rates["text_out"] is None:
            image_out, text_out = out_total, 0  # model has no text output -> all output tokens are image tokens
        else:
            return None, "usage lacks output_tokens_details; image/text output split unknown for this model"
    except (KeyError, TypeError, ValueError):
        return None, "usage data malformed or incomplete"
    if min(text_in, image_in, image_out, text_out) < 0:
        return None, "usage data contains negative token counts"
    if text_out and rates["text_out"] is None:
        return None, "usage reports text output tokens but no documented text-output price exists for this model"
    amount = (text_in * rates["text_in"] + image_in * rates["image_in"] + image_out * rates["image_out"]
              + text_out * (rates["text_out"] or 0.0)) / 1_000_000
    basis = (f"usage x documented token rates for {_base_model(model)} (retrieved {PRICING_RETRIEVED_ON}, "
             f"{PRICING_SOURCE_TOKENS}): text_in={text_in}, image_in={image_in}, image_out={image_out}, text_out={text_out}")
    return round(amount, 6), basis
