"""
Generation service: the only place the rest of the module touches an
AIImageProvider. Responsibilities:
  * request validation (no credentials in prompts, sane quality/resolution)
  * per-project budget guard (spec §10/§24) -- known costs, plus the pre-call
    estimate of any generation whose real cost is UNKNOWN (conservative)
  * bounded retry for transient failures of NON-billable providers; billable providers are
    resent only when the provider demonstrably did not process the request (off by default),
    and a timeout / ambiguous outcome is recorded as UNCERTAIN, never retried or fallen back
  * output verification BEFORE anything is marked SUCCEEDED
  * honest classification: generation_kind AI_GENERATED | PLACEHOLDER | TEST_FIXTURE
    on Asset, GenerationRecord and CostRecord; cost_status KNOWN | UNKNOWN | NOT_APPLICABLE
  * explicit, labelled placeholder fallback -- only when the caller asked for it
    (or the operator configured it); a real-AI request is never silently fulfilled
    with a placeholder
"""
from __future__ import annotations

import re
import time

from PIL import Image
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Asset, CostRecord, GenerationRecord, Project
from app.errors import VideoModuleError
from app.generation.base import (
    ALLOWED_ASPECTS, ALLOWED_PURPOSES, ALLOWED_QUALITIES, COST_KNOWN, COST_NOT_APPLICABLE, COST_UNKNOWN, GEN_AI,
    GEN_FIXTURE, GEN_PLACEHOLDER, PROVIDER_ERROR_CODES, STATE_NOT_CONFIGURED, STATE_SUCCEEDED, STATE_UNCERTAIN,
    TRANSIENT_PROVIDER_CODES, AIImageProvider, ImageGenerationRequest, ProviderError, parse_resolution, state_for_code,
)
from app.generation.registry import PLACEHOLDER_PROVIDER, get_provider
from app.logging_conf import get_logger
from app.pipeline.import_assets import sanitize_text, sha256_of_file, write_source_metadata
from app.storage import to_rel

logger = get_logger("generation")

_CREDENTIAL_RE = re.compile(r"(?i)(api[_-]?key|password|secret|bearer\s+[a-z0-9._-]{8,}|\bsk-[a-z0-9_-]{8,})")
# failures of a real-AI request that an explicit fallback may cover
FALLBACK_ELIGIBLE_CODES = PROVIDER_ERROR_CODES | {"BUDGET_EXCEEDED"}
PLACEHOLDER_LICENCE = "Module-generated placeholder (no third-party rights)"


def validate_request(req: ImageGenerationRequest) -> ImageGenerationRequest:
    prompt = sanitize_text(req.prompt, 2000) or ""
    if not prompt:
        raise VideoModuleError("VALIDATION_ERROR", "Prompt is required for generation.")
    if _CREDENTIAL_RE.search(prompt):
        raise VideoModuleError("VALIDATION_ERROR", "Prompt appears to contain a credential; credentials must never be sent in prompts.")
    if req.quality not in ALLOWED_QUALITIES:
        raise VideoModuleError("VALIDATION_ERROR", f"quality must be one of {sorted(ALLOWED_QUALITIES)}")
    if req.aspect_ratio not in ALLOWED_ASPECTS:
        raise VideoModuleError("VALIDATION_ERROR", f"aspect_ratio must be one of {sorted(ALLOWED_ASPECTS)}")
    if req.purpose is not None and req.purpose not in ALLOWED_PURPOSES:
        raise VideoModuleError("VALIDATION_ERROR", f"purpose must be one of {sorted(ALLOWED_PURPOSES)}")
    try:
        parse_resolution(req.resolution)
    except (ValueError, AttributeError) as exc:
        raise VideoModuleError("VALIDATION_ERROR", "resolution must look like 1280x720 (64..4096 per side).") from exc
    req.prompt = prompt
    return req


def project_spend(db: Session, project_id: str) -> float:
    """Sum of KNOWN costs only (what was actually computed from provider usage)."""
    total = db.query(func.coalesce(func.sum(CostRecord.estimated_cost), 0.0)).filter(
        CostRecord.project_id == project_id, CostRecord.cost_status == COST_KNOWN
    ).scalar()
    return float(total or 0.0)


def budget_commitment(db: Session, project_id: str) -> tuple[float, int]:
    """(known spend + pre-call estimates of UNKNOWN-cost generations, count of UNKNOWN-cost generations).
    Used only for the budget guard, so unknown costs are never treated as free."""
    known = project_spend(db, project_id)
    unknown_rows = db.query(CostRecord.pre_call_estimate_usd).filter(
        CostRecord.project_id == project_id, CostRecord.cost_status == COST_UNKNOWN).all()
    return known + sum(r[0] or 0.0 for r in unknown_rows), len(unknown_rows)


def fallback_allowed(requested: bool | None) -> bool:
    return bool(requested) if requested is not None else bool(get_settings().allow_placeholder_fallback)


def estimate(provider_name: str, req: ImageGenerationRequest) -> dict:
    provider = get_provider(provider_name)
    validate_request(req)
    est = provider.estimate_cost(req)
    return {
        "provider": provider.name, "model": provider.model, "is_mock": provider.is_mock,
        "generation_kind": provider.generation_kind, "is_real_ai": provider.generation_kind == GEN_AI,
        "provider_state": provider.config_state(), "billable": provider.billable,
        "estimated_cost": est, "estimate_status": ("NOT_APPLICABLE" if not provider.billable
                                                   else ("ESTIMATED" if est is not None else "UNKNOWN")),
        "estimate_basis": provider.estimate_basis(req), "currency": "USD",
    }


def ensure_requestable(provider_name: str, allow_fallback: bool | None) -> AIImageProvider:
    """Submit-time check: a real-AI request to an unconfigured provider fails clearly unless fallback was opted into."""
    provider = get_provider(provider_name)
    if provider.config_state() == STATE_NOT_CONFIGURED and not fallback_allowed(allow_fallback):
        raise VideoModuleError("PROVIDER_NOT_CONFIGURED",
                               f"Image provider '{provider.name}' is not configured (missing credentials). No request was "
                               "made. Configure it (docs/PROVIDER_SETUP.md), choose another provider, or set "
                               "allow_placeholder_fallback=true to accept a clearly labelled PLACEHOLDER instead.")
    return provider


def _fail(db: Session, gen: GenerationRecord, code: str, message: str, state: str | None = None,
          provider_request_id: str | None = None) -> VideoModuleError:
    gen.status = "FAILED"
    gen.provider_state = state or state_for_code(code)
    gen.error_code = code
    gen.error_message = message[:2000]
    if provider_request_id:
        gen.provider_request_id = provider_request_id[:128]
    db.commit()
    logger.warning("Generation %s failed: %s %s", gen.generation_id, code, message)
    return VideoModuleError(code, message)


def _fail_uncertain(db: Session, gen: GenerationRecord, project_id: str, provider: AIImageProvider,
                    request: ImageGenerationRequest, projected: float | None, exc: ProviderError,
                    attempts: int) -> VideoModuleError:
    """No result was received, but the provider may have processed (and billed) the request.
    Recorded as FAILED with provider_state UNCERTAIN; if billable, an UNKNOWN-cost CostRecord (no asset)
    keeps the possible charge visible in the cost report and counted by the budget guard."""
    if provider.billable:
        gen.cost_status = COST_UNKNOWN
        db.add(CostRecord(
            project_id=project_id, generation_id=gen.generation_id, asset_id=None, provider=provider.name,
            is_mock=provider.is_mock, model=provider.model, quality=request.quality, resolution=request.resolution,
            estimated_cost=None, generation_kind=provider.generation_kind, cost_status=COST_UNKNOWN,
            cost_basis="outcome UNCERTAIN (no response received); the provider may have billed this request -- "
                       "actual cost UNKNOWN", pre_call_estimate_usd=projected,
            provider_request_id=exc.provider_request_id))
    err = _fail(db, gen, exc.code, f"{exc.message} (after {attempts} attempt(s))", STATE_UNCERTAIN,
                exc.provider_request_id)
    err.outcome_uncertain = True
    return err


def generate_image(
    db: Session, *, project_id: str, provider_name: str, request: ImageGenerationRequest,
    job_id: str | None = None, cancel_check=None, allow_fallback: bool | None = None,
) -> tuple[Asset, GenerationRecord]:
    project = db.get(Project, project_id)
    if project is None:
        raise VideoModuleError("NOT_FOUND", f"Project {project_id} not found.")
    provider = get_provider(provider_name)
    request = validate_request(request)
    try:
        return _generate_once(db, project, provider, request, job_id=job_id, cancel_check=cancel_check)
    except VideoModuleError as exc:
        # never fall back after an UNCERTAIN outcome: the real request may have been processed and billed
        if (provider.generation_kind == GEN_AI and exc.code in FALLBACK_ELIGIBLE_CODES
                and not getattr(exc, "outcome_uncertain", False) and fallback_allowed(allow_fallback)):
            logger.info("Real AI generation via %s failed (%s); explicit placeholder fallback requested", provider.name, exc.code)
            return _generate_once(db, project, get_provider(PLACEHOLDER_PROVIDER), request, job_id=job_id,
                                  cancel_check=cancel_check, fallback_from=provider.name,
                                  fallback_reason=f"{exc.code}: {exc.message}"[:1000])
        raise


def _generate_once(db: Session, project: Project, provider: AIImageProvider, request: ImageGenerationRequest, *,
                   job_id: str | None, cancel_check, fallback_from: str | None = None,
                   fallback_reason: str | None = None) -> tuple[Asset, GenerationRecord]:
    settings = get_settings()
    project_id = project.project_id
    projected = provider.estimate_cost(request) if provider.billable else None
    gen = GenerationRecord(
        project_id=project_id, job_id=job_id, prompt=request.prompt, aspect_ratio=request.aspect_ratio,
        resolution=request.resolution, quality=request.quality, purpose=request.purpose,
        scene_id=request.scene_id, reference_assets=list(request.reference_assets or []),
        provider=provider.name, is_mock=provider.is_mock, model=provider.model, status="PENDING",
        generation_kind=provider.generation_kind, pre_call_estimate_usd=projected, estimated_cost=None,
        is_fallback=fallback_from is not None, fallback_from=fallback_from, fallback_reason=fallback_reason,
    )
    db.add(gen)
    db.commit()

    if provider.config_state() == STATE_NOT_CONFIGURED:
        raise _fail(db, gen, "PROVIDER_NOT_CONFIGURED",
                    f"Image provider '{provider.name}' is not configured (missing credentials); no request was made.")
    if provider.billable:
        committed, unknown_n = budget_commitment(db, project_id)
        if projected is None:
            raise _fail(db, gen, "BUDGET_EXCEEDED",
                        f"No documented price for {provider.name}/{provider.model}; the budget cannot be checked, so the "
                        "billable request was not sent.", state="FAILED")
        if committed + projected > project.ai_image_budget_usd + 1e-9:
            raise _fail(db, gen, "BUDGET_EXCEEDED",
                        f"AI image budget would be exceeded: committed {committed:.4f} (known costs"
                        f"{f' + estimates of {unknown_n} unknown-cost generation(s)' if unknown_n else ''}) + estimate "
                        f"{projected:.4f} > budget {project.ai_image_budget_usd:.4f} USD.", state="FAILED")

    out_dir = settings.generated_dir / project_id
    result = None
    attempts = 0
    retries = settings.billable_generation_max_retries if provider.billable else settings.generation_max_retries
    max_attempts = 1 + max(0, retries)
    while attempts < max_attempts:
        attempts += 1
        gen.attempts = attempts
        db.commit()
        if cancel_check:
            cancel_check()
        try:
            result = provider.generate(request, out_dir)
            break
        except ProviderError as exc:
            # a billable request is resent only when the provider demonstrably did not process it
            retryable = (exc.not_processed if provider.billable
                         else exc.code in TRANSIENT_PROVIDER_CODES and not exc.outcome_uncertain)
            if retryable and attempts < max_attempts:
                logger.info("Transient %s on attempt %d for generation %s; retrying", exc.code, attempts, gen.generation_id)
                time.sleep(settings.generation_retry_backoff_s * attempts)
                continue
            if exc.outcome_uncertain:
                raise _fail_uncertain(db, gen, project_id, provider, request, projected, exc, attempts) from exc
            raise _fail(db, gen, exc.code, f"{exc.message} (after {attempts} attempt(s))", exc.state,
                        exc.provider_request_id) from exc
        except Exception as exc:  # noqa: BLE001
            raise _fail(db, gen, "GENERATION_FAILED", f"Provider error: {type(exc).__name__}") from exc

    # ---- verify real output before recording success (never fabricate success)
    fp = result.file_path
    try:
        fp_res = fp.resolve()
        if out_dir.resolve() not in fp_res.parents:
            raise _fail(db, gen, "GENERATION_FAILED", "Provider wrote output outside the generated/ directory.")
        if not fp_res.exists() or fp_res.stat().st_size == 0:
            raise _fail(db, gen, "GENERATION_FAILED", "Provider reported success but output file is missing/empty.")
        with Image.open(fp_res) as im:
            im.load()
            real_w, real_h = im.size
        if (real_w, real_h) != (result.width, result.height):
            raise _fail(db, gen, "GENERATION_FAILED",
                        f"Output dimensions {real_w}x{real_h} do not match provider claim {result.width}x{result.height}.")
    except VideoModuleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _fail(db, gen, "GENERATION_FAILED", f"Output is not a decodable image: {type(exc).__name__}") from exc

    # ---- classification: the PROVIDER's declared kind wins; a result can never upgrade itself to real AI
    kind = provider.generation_kind
    if result.is_mock or provider.is_mock:
        kind = GEN_FIXTURE
    cost = result.cost
    if kind != GEN_AI or not provider.billable:
        cost_status, amount, cost_basis = COST_NOT_APPLICABLE, None, (cost.basis if cost else "no billable provider")
    elif cost is not None and cost.status == COST_KNOWN and cost.amount is not None:
        cost_status, amount, cost_basis = COST_KNOWN, float(cost.amount), cost.basis
    else:
        cost_status, amount = COST_UNKNOWN, None
        cost_basis = (cost.basis if cost else "provider returned no cost/usage data") + "; actual cost UNKNOWN"
    usage = cost.usage if cost else None

    fb = fallback_from is not None
    if kind == GEN_PLACEHOLDER:
        asset_type, source_type, editorial = "PLACEHOLDER_IMAGE", "SYSTEM_GENERATED", "PLACEHOLDER"
        title_prefix = "[FALLBACK PLACEHOLDER] " if fb else "[PLACEHOLDER] "
        description = (f"PLACEHOLDER image (procedural, not AI, not evidence; purpose={request.purpose or 'ILLUSTRATION'})."
                       + (f" Fallback after {fallback_from} failed." if fb else ""))
        licence = PLACEHOLDER_LICENCE
        attribution = "PLACEHOLDER image (procedural, not AI, not evidence)" + (f" - fallback for {fallback_from}" if fb else "")
    else:
        asset_type, source_type, editorial = "AI_IMAGE", "AI_GENERATED", "AI_GENERATED"
        title_prefix = "[MOCK] " if kind == GEN_FIXTURE else ""
        description = f"Illustrative AI visual (purpose={request.purpose or 'ILLUSTRATION'}). Not evidence."
        licence = "AI-generated by module provider"
        attribution = (f"AI-generated illustration ({result.provider}{' MOCK' if kind == GEN_FIXTURE else ''}) - "
                       "not a photograph of real events")

    asset = Asset(
        project_id=project_id,
        asset_type=asset_type,
        source_type=source_type,  # spec §8: always explicit
        local_path=to_rel(fp_res),
        mime_type=result.mime_type or "image/png",
        file_size_bytes=fp_res.stat().st_size,
        title=(title_prefix + request.prompt)[:255],
        description=description,
        creator=f"{result.provider}/{result.model}",
        licence=licence,
        attribution=attribution,
        rights_status="PERMITTED",
        checksum_sha256=sha256_of_file(fp_res),
        width=real_w,
        height=real_h,
        provider=provider.name,
        model=result.model,
        generation_cost=amount,
        is_mock=kind == GEN_FIXTURE,
        editorial_category=editorial,
        generation_kind=kind,
        cost_status=cost_status,
        is_fallback=fb,
        fallback_from=fallback_from,
        fallback_reason=fallback_reason,
        provider_request_id=(result.provider_request_id or None),
        extra_metadata={
            "is_mock": kind == GEN_FIXTURE, "generation_kind": kind, "purpose": request.purpose,
            "prompt": request.prompt, "generation_id": gen.generation_id, "tags": _prompt_tags(request.prompt),
            "is_fallback": fb, **({"fallback_from": fallback_from} if fb else {}), **(result.metadata or {}),
        },
    )
    db.add(asset)
    db.flush()

    gen.asset_id = asset.asset_id
    gen.model = result.model
    gen.request_id = result.request_id
    gen.provider_request_id = result.provider_request_id
    gen.status = "SUCCEEDED"
    gen.provider_state = STATE_SUCCEEDED
    gen.generation_kind = kind
    gen.generation_time_seconds = result.generation_time_seconds
    gen.estimated_cost = amount
    gen.cost_status = cost_status
    gen.currency = result.currency
    gen.provider_metadata = result.metadata
    db.add(CostRecord(
        project_id=project_id, generation_id=gen.generation_id, asset_id=asset.asset_id,
        provider=provider.name, is_mock=kind == GEN_FIXTURE, model=result.model, quality=request.quality,
        resolution=request.resolution, estimated_cost=amount, currency=result.currency,
        request_id=result.request_id, generation_kind=kind, cost_status=cost_status, cost_basis=cost_basis[:2000],
        usage=usage, pre_call_estimate_usd=projected, provider_request_id=result.provider_request_id, is_fallback=fb,
    ))
    db.commit()
    write_source_metadata(asset)
    logger.info("Generated asset %s via %s kind=%s fallback=%s cost_status=%s", asset.asset_id, provider.name, kind, fb,
                cost_status)
    return asset, gen


def _prompt_tags(prompt: str) -> list[str]:
    words = re.findall(r"[a-z]{4,}", prompt.lower())
    return sorted(set(words))[:30]
