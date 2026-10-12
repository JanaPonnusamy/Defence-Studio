# Image Provider Setup

All external providers are **optional**. With no credentials and no network, the module starts and works fully. The only change is that real AI image generation reports `NOT_CONFIGURED`.

## Providers

| Name | `generation_kind` | What it is | Credentials | Cost |
|---|---|---|---|---|
| `placeholder-procedural` (alias `local-procedural`) | `PLACEHOLDER` | Local deterministic procedural renderer. **Not AI, never evidence.** Every image carries the band "PLACEHOLDER - NOT AI - NOT EVIDENCE". This is the default. | none | `NOT_APPLICABLE` |
| `mock-local` | `TEST_FIXTURE` | 64×36 "MOCK" image for automated tests | none | `NOT_APPLICABLE` |
| `openai-images` | `AI_GENERATED` | OpenAI Images API, `POST /v1/images/generations` | `OPENAI_API_KEY` | **billable**; see "Cost handling" |

`GET /providers` lists each provider with `generation_kind`, `is_real_ai`, `billable`, `state` and `adapter_contract_version` (`2.0`).

## Why OpenAI Images

The spec (§22) names OpenAI as the reference example of a provider-neutral adapter. The audit was done on 2026-10-12 against the official docs:

- **Endpoint.** The documented REST endpoint takes a plain JSON body and returns base64 image data (`data[].b64_json`). It needs only `httpx`, which is already a dependency, so no SDK is added.
- **Usage data.** The response carries `usage` (input/output token counts), and pricing is published per token. That lets the module compute a real cost from actual usage instead of guessing.
- **Model.** The default is `gpt-image-2`, snapshot `gpt-image-2-2026-04-21`. The independent audit on 2026-10-12 found that `gpt-image-1` is scheduled for shutdown on **2026-10-23**, and `gpt-image-1.5`, `gpt-image-1-mini` and `chatgpt-image-latest` on **2026-12-01**, according to the official deprecations page. The newer `gpt-image-2.5-*` models are not in the module's pricing table, so they would be refused by the budget guard. The model page lists support for `v1/images/generations`. The model is configurable via `VIDEO_MODULE_OPENAI_IMAGE_MODEL`.

Sources (official, retrieved 2026-10-12):
- Image generation guide: <https://developers.openai.com/api/docs/guides/image-generation>
- gpt-image-2 model page: <https://developers.openai.com/api/docs/models/gpt-image-2>
- Pricing: <https://developers.openai.com/api/docs/pricing>
- Response types (official SDK source): <https://github.com/openai/openai-python/blob/main/src/openai/types/images_response.py>

Other providers were not implemented. Adding one means writing a new `AIImageProvider` in `app/generation/` and registering it in `registry.py`. The pipeline never calls provider code directly; a test enforces this.

## Enabling openai-images

1. Set the key in the process environment or the module's `.env` file (never commit it):
   - `OPENAI_API_KEY=...` (or `VIDEO_MODULE_OPENAI_API_KEY=...`)
2. Optional settings:
   - `VIDEO_MODULE_OPENAI_IMAGE_MODEL` (default `gpt-image-2`)
   - `VIDEO_MODULE_OPENAI_BASE_URL` (default `https://api.openai.com/v1`)
   - `VIDEO_MODULE_OPENAI_TIMEOUT_S` (default 180)
   - `VIDEO_MODULE_BILLABLE_GENERATION_MAX_RETRIES` (default **0**). See "Retry policy" below.
3. Check the configuration. This never generates an image:
   - `GET /providers/openai-images/status` checks configuration only and makes no network request.
   - `GET /providers/openai-images/status?probe=true` makes one **non-generating, non-billable** call, `GET /v1/models/{model}`, and only when a key is set. The result is `AVAILABLE`, `FAILED` (auth or model not visible) or `UNAVAILABLE` (network, 429 or 5xx).

How the key is handled:
- It is held as a pydantic `SecretStr` and sent only in the `Authorization` header.
- It is never logged and never returned by any endpoint.
- It is scrubbed from provider error text before that text is stored or raised. Tests cover this.

## States and error codes

| State | When | Error code(s) |
|---|---|---|
| `NOT_CONFIGURED` | No key. **No request is made.** | `PROVIDER_NOT_CONFIGURED` (409 at submit time) |
| `UNAVAILABLE` | Could not connect (request never sent), 429 rate limit, or 5xx | `PROVIDER_UNAVAILABLE`, `PROVIDER_RATE_LIMITED` |
| `UNCERTAIN` | Timeout or connection dropped **after the request was sent**: OpenAI may have processed and billed it. Never retried, never replaced by a placeholder; cost recorded as `UNKNOWN` (counted in `uncertain_outcome_count` and by the budget guard). | `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE` |
| `FAILED` | Provider answered but generation failed | `PROVIDER_AUTH_FAILED` (401/403), `PROVIDER_QUOTA` (429 `insufficient_quota`, not retried), `PROVIDER_CONTENT_REJECTED` (moderation), `PROVIDER_RESPONSE_INVALID` (malformed/no `b64_json`/undecodable image), `GENERATION_FAILED`, `BUDGET_EXCEEDED` |
| `SUCCEEDED` | Image decoded and verified on disk | — |

### Retry policy (billable providers)

- **Default: no automatic retries.** Every billable generation is sent at most once.
- If `VIDEO_MODULE_BILLABLE_GENERATION_MAX_RETRIES` is raised, only failures where OpenAI demonstrably did not process the request are resent: connection never established (`ConnectError`, `ConnectTimeout`, `PoolTimeout`) and an explicit 429 rate-limit rejection.
- Never resent, whatever the setting: read/write timeouts, connections dropped after sending, and 5xx responses.
- An `UNCERTAIN` failure is not converted into a placeholder, even with `allow_placeholder_fallback=true`. Resubmitting is a new, explicit request; check the usage dashboard first.
- Non-billable providers (placeholder, mock) keep the old bounded retry (`VIDEO_MODULE_GENERATION_MAX_RETRIES`, default 2).
- Covered by `tests/test_timeout_no_retry.py` (mocked HTTP; asserts exactly one generation POST on every ambiguous failure).

The state is stored on each generation record as `provider_state`. It is exposed by `GET /generations?project_id=` and `GET /generations/{id}`, and in `failed_by_provider_state` in the cost report.

What is recorded for a successful generation:
- provider and model
- the module `request_id`, plus the provider `x-request-id` (`provider_request_id`)
- `provider_created` timestamp
- real decoded dimensions; the requested size and quality
- `revised_prompt`, the raw `usage` block and the asset ID
- `c2pa_manifest_present`: detection of a PNG C2PA chunk only. The manifest is not validated.

Output size: the adapter always requests one of the documented preset sizes, so the documented price table applies. It picks `1536x1024` for landscape requests, `1024x1536` for portrait and `1024x1024` for square. The real decoded dimensions are what gets stored.

Quality mapping: `draft` → `low`, `standard` → `medium`, `high` → `high`.

## Fallback (explicit only)

A request to a real AI provider is **never** silently fulfilled with a placeholder. Fallback to `placeholder-procedural` happens only when one of these is true:

- the request sets `allow_placeholder_fallback: true` (`POST /assets/generate`, or build option `allow_placeholder_fallback`), or
- the operator sets `VIDEO_MODULE_ALLOW_PLACEHOLDER_FALLBACK=true`. An explicit `false` on the request overrides this.

A fallback asset has:
- `generation_kind: PLACEHOLDER`, `is_fallback: true`, `fallback_from` and `fallback_reason`
- a title starting with `[FALLBACK PLACEHOLDER]`
- the on-screen overlay "PLACEHOLDER IMAGE - NOT AI - NOT EVIDENCE (FALLBACK)"

The failed real-AI attempt stays recorded with its own `provider_state`.

## Cost handling

- **Recorded cost** is `KNOWN` only when it is computed as the documented per-1M-token rates multiplied by the `usage` token counts returned in the response. The rates, retrieved 2026-10-12, live in `app/generation/openai_pricing.py`.
  - gpt-image-2: text input $5.00, image input $8.00, image output $30.00
  - gpt-image-1.5: $5.00 / $8.00 / $32.00, plus text output $10.00
  - gpt-image-1: $5.00 / $10.00 / $40.00
  - gpt-image-1-mini: $2.00 / $2.50 / $8.00
- **UNKNOWN cost.** If `usage` is missing or ambiguous, or the model isn't in the table, the cost is `UNKNOWN` and the amount is `null`. It is never guessed and never summed as zero.
- **Pre-call budget estimate.** This comes only from the documented per-image output price for the preset size and quality. It excludes the few prompt text-input tokens. Examples for gpt-image-2:
  - low: $0.006 (1024²) / $0.005 (1536×1024)
  - medium: $0.053 / $0.041
  - high: $0.211 / $0.165
- **Budget guard.** A billable request is not sent if the known spend, plus the pre-call estimate of each earlier UNKNOWN-cost generation, plus this estimate, would exceed the project budget. If no documented price exists, it is not sent at all.
- **Placeholder and mock.** These are `NOT_APPLICABLE` with no amount. The previous build's invented "USD 0.01 per image" rate for the local renderer has been removed, and migration 0002 nulls it out of existing rows.

**Prices change.** Re-verify against the pages above and update `PRICING_RETRIEVED_ON` and the tables in `openai_pricing.py`.

## Tests

The normal suite **never** makes a network request to a provider:
- `tests/conftest.py` neutralises any key. It sets the key variables to empty strings, which override a key in the module `.env`, and forces the cached setting to `None`. The self-test and browser check do the same for the servers they start (audit fix, 2026-10-12).
- It also installs an `httpx.MockTransport` that fails the test if the adapter tries to reach the network.

`tests/test_openai_provider.py` covers success (known cost from usage), success without usage (unknown cost), auth failure, quota, rate limit, 5xx, timeout, connection error, five malformed-response cases, not-configured, explicit and configured fallback, the connectivity probe and key redaction. All of it runs against mocked HTTP.

**Opt-in live tests** are in `tests/test_provider_live_optin.py` and are **skipped by default**:

```powershell
$env:VVM_ENABLE_PAID_PROVIDER_TESTS = "1"; $env:OPENAI_API_KEY = "<your key>"
.\.venv\Scripts\python.exe -m pytest tests\test_provider_live_optin.py      # connectivity only (GET /models, not billable)
$env:VVM_LIVE_GENERATE = "1"                                               # additionally: ONE billable low-quality image
.\.venv\Scripts\python.exe -m pytest tests\test_provider_live_optin.py
```

**The single paid live test** is run with `docs/LIVE_PROVIDER_TEST_RUNBOOK.md` and `scripts/live_provider_check.py`. These use throwaway storage, a dry-run default, `--confirm-paid`, a cost cap, zero retries, and a guard that allows only one generation POST.

**Status of this build:** the adapter is implemented and unit-tested against mocked HTTP only. It has **not** been exercised against the live API. No key was available and paid calls were not authorised, so **zero** real or paid requests were made.
