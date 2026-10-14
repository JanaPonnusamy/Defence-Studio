# Phase 1 — Content Search & Discovery: Implementation Specification

**Status: specification for review. Nothing here is implemented.** Official phase: Core Design Phase 1A (feed) + 1B (topic search). Roadmap step RM-1 (`DEFENCE_ERA_IMPLEMENTATION_ROADMAP.md`). Closes pending items B1, B2, B3 and the Phase 1 part of D5.

## 1. Baseline this spec extends (verified 2026-10-14)

The discovery stage already exists and must be **extended, not replaced**:

- Stage `news_feed_and_topic_discovery`; request schema `defence-studio.discovery-request.v1`, result `defence-studio.discovery-result.v1` (`contracts/discovery.py`).
- Routes: `POST/GET /api/jobs/{id}/discovery/requests`, `GET .../candidates` (filters: `language`, `source_type`, `provider`, `min_score`, `query`), `GET .../candidates/{id}`, `POST/GET .../discovery/selection`.
- Providers: `rss_feed` (needs `DEFENCE_STUDIO_DISCOVERY_RSS_FEEDS`; none configured by default) and `configured_search_api` (needs `DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL` + `_KEY`; reports `UNAVAILABLE_MISSING_CREDENTIALS` today, never run against a real service).
- Provider statuses: OK, PARTIAL, COMPLETED_NO_RESULTS, UNAVAILABLE_MISSING_CREDENTIALS, UNAVAILABLE_NOT_CONFIGURED, UNAVAILABLE_OPTIONAL_DEPENDENCY_MISSING, ERROR, DISABLED.
- Limits: `max_results` 10, `timeout_seconds` 20, `max_redirects` 5, `max_bytes_per_fetch` 2 MiB, clamped by hard ceilings in `discovery/service.py`.
- Candidate fields: declared title/publisher/published_at/language each with a `MetadataStatus`, ranking factors with explanations, `merged_from_providers`.
- UI: `frontend/js/views/discovery-view.js` (topic, language, candidates, selection).
- Tests: `test_discovery_f1a.py` (26) within the 287 passing.

## 2. Gaps this phase closes

1. No real topic-search provider has ever run.
2. No defence category taxonomy; no category, country/region or publisher filter; the listing API has no date filter.
3. No pagination; no persistent search history UI; no per-provider rate limiting or retry policy.
4. No "source class" (official / independent reporting / expert analysis / company release).
5. No visible provider coverage/failure panel.

## 3. Decisions the user must make before coding

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Search provider for `configured_search_api` | A hosted news/search API (user supplies URL + key), or RSS-only curated feeds for v1 | Keep the adapter contract provider-neutral; use RSS plus **one** user-chosen search API. Do not hard-code a vendor. |
| D2 | Initial curated feed list per category | User supplies feed URLs (official MoD/PIB, Indian media, Defense News, BBC, conflict reporting) | User-supplied; stored in config, not code. |
| D3 | Search API cost ceiling per day | A number, or "free tier only" | Required if the provider is paid. |

Until D1 is answered the provider is **not configured**, the UI says so, and no result is invented.

## 4. Scope

**In:** search UI and navigation; filters; provider abstraction and configuration status; result schema additions; pagination; cross-provider deduplication of candidates (URL canonicalization and title+publisher near-match, extending existing `merged_from_providers`); timeouts, rate limits, retry; error/empty/loading/partial-failure states; persistent search history; API and DB changes; tests.

**Out (explicit exclusions):** fetching article bodies (F3), extraction/normalization (F4), claim analysis, review, package building, script generation, translation, TTS, scenes, assets, rendering, YouTube. Selecting candidates only feeds the existing selection endpoint; nothing is acquired in this phase.

## 5. Search UI and navigation

- Existing tab `discovery` becomes **Search**: a search form at top, filters panel, results list, provider-coverage strip, history drawer.
- Inputs: topic (required, ≤1000 chars), keywords (optional, comma separated, chips), date range (from/to, ISO), category (multi-select), country/region (multi-select), language (`en`, `ta`, any), source class (multi-select), providers (multi-select showing configured state).
- Reuse UI primitives per the component registry (text field, date picker); no per-page re-implementation. A date picker primitive is PLANNED in `REGISTRY.md` and is built once here if not already present.
- Result card: title, publisher, publication time (with `MetadataStatus`), retrieval time, category and region tags, source class, language, summary snippet if the provider declared one, canonical link, providers that returned it, ranking explanation, select checkbox.
- States: **idle**, **loading** (per-provider progress, cancel button), **results**, **empty** ("No results from N configured providers" — never implies the web has none), **partial failure** (banner listing failed providers and reasons; successful results remain), **all unavailable** (shows which env settings/config are missing, never secret values), **error**.
- Mobile 320–1440 px; keyboard accessible; text escaped (existing `escapeHtml` pattern).
- Never labels coverage "exhaustive".

## 6. Categories and filters

Initial category set (configurable list, not hard-coded in providers): `INDIAN_DEFENCE`, `INTERNATIONAL_DEFENCE`, `RUSSIA_UKRAINE`, `PROCUREMENT_INDUSTRY`, `NEIGHBOURHOOD`, `OTHER`. A candidate's category comes from (in order): the feed's configured category, a provider-declared tag, deterministic keyword rules; unknown stays `UNCLASSIFIED`, never guessed by an LLM in this phase. Each assignment records `category_basis` (`FEED_CONFIG`, `PROVIDER_TAG`, `KEYWORD_RULE`, `UNCLASSIFIED`).

Country/region tags follow the same rule with `region_basis`. Source class values: `OFFICIAL`, `INDEPENDENT_REPORTING`, `EXPERT_ANALYSIS`, `COMPANY_RELEASE`, `UNKNOWN`, declared per configured feed/domain; default `UNKNOWN`.

Filters are applied server-side. A filter a provider cannot honour is reported in that provider's outcome (`filters_not_supported: [...]`) and applied to its results afterwards **only when the underlying metadata exists**; otherwise results lacking the metadata are shown and marked `filter_unverifiable`, not silently dropped or silently kept.

## 7. Provider abstraction and configuration

- Keep `DiscoveryProvider.discover(context)` and `DiscoveryProviderRegistry`; add `describe()` returning `{name, kind, configured, requires_credentials, supported_filters, rate_limit, status_message}` without any secret values.
- Add `GET /api/discovery/providers` (owner-authenticated) returning those descriptors, so the UI can show what is configured.
- Configuration: environment variables for URL/key (existing names); feed list and category/source-class mapping in a JSON config file under `backend/config/` or `DEFENCE_STUDIO_DISCOVERY_CONFIG`; schema-validated at startup; invalid config fails closed with a clear message. Per-user credentials are a separate item (X2) and out of scope.
- All outbound fetches go through the existing SSRF-guarded `GuardedFetcher`.

## 8. Result schema (additive, `defence-studio.discovery-result.v2` proposed)

Existing fields unchanged. Added per candidate: `category`, `category_basis`, `regions[]`, `region_basis`, `source_class`, `retrieved_at` (ISO UTC, set by the server at fetch), `search_run_id`, `provider_rank`, `snippet` (provider-declared, ≤500 chars), `filter_flags[]`. Per provider outcome: `duration_ms`, `http_attempts`, `rate_limited` (bool), `filters_applied[]`, `filters_not_supported[]`, `truncated` (bool).

`published_at` stays optional; absence is `MetadataStatus.NOT_COLLECTED`, never today's date. v1 results remain readable; the version is carried in the artifact, with a reader for both.

## 9. Pagination, deduplication, timeouts, rate limits, retries

- **Pagination:** `GET .../candidates?cursor=&page_size=` (default 20, max 100), stable ordering `(total_score desc, published_at desc, candidate_id)`; response includes `next_cursor` and `total_count`. `limits.max_results` bounds what a run collects (ceiling raised only with a recorded reason).
- **Dedup:** canonical URL (strip tracking parameters such as `utm_*`, fragments, trailing slash, lowercase host), then same-publisher title similarity ≥ threshold plus same-day publication. Merge, never delete: keep `merged_from_providers` and the member URLs. Syndicated copies on different publishers are **not** merged here (that judgement belongs to F4).
- **Timeouts:** per request 20 s default (existing), per provider total budget `timeout_seconds × feeds`, per search run wall-clock cap (config, default 60 s). Cancel honoured.
- **Rate limits:** per-provider token bucket from provider descriptor (default 1 request/s, configurable); a 429 with `Retry-After` is honoured up to a cap; the provider reports `rate_limited`.
- **Retries:** idempotent GETs only, max 2 retries with exponential backoff and jitter on connection errors and 5xx/429; none on 4xx; no retry of a response already received. Total attempts recorded in the outcome.
- **Partial failure:** the run status is `COMPLETED` (all providers OK or empty), `COMPLETED_WITH_FAILURES`, or `FAILED` (all unavailable/error). Results from successful providers are always kept.

## 10. Persistent search history

Each submitted request already persists as an artifact on the job. Add table `discovery_searches` (migration schema v8, additive `CREATE TABLE IF NOT EXISTS`): `search_run_id`, `job_id`, `owner_id`, `topic`, `filters_json`, `status`, `created_at`, `completed_at`, `candidate_count`, `provider_outcomes_json`, `request_sha256`. `GET /api/jobs/{id}/discovery/searches` lists; `POST .../searches/{id}/rerun` creates a **new** run (history is immutable). Owner isolation matches existing job rules. No secrets stored.

## 11. API contracts (new or changed)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/discovery/providers` | provider descriptors, no secrets |
| GET | `/api/discovery/taxonomy` | categories, regions, source classes |
| POST | `/api/jobs/{id}/discovery/requests` | accepts new filter keys (`categories`, `regions`, `source_classes`, `keywords`); unknown keys → 422 |
| GET | `/api/jobs/{id}/discovery/candidates` | adds `category`, `region`, `source_class`, `date_from`, `date_to`, `publisher`, `cursor`, `page_size` |
| GET | `/api/jobs/{id}/discovery/searches` | history |
| POST | `/api/jobs/{id}/discovery/searches/{sid}/rerun` | new run |

Errors use the existing typed envelope; no stack traces or provider URLs containing keys in responses or logs.

## 12. Tests

**Unit:** query building; canonical URL; dedup thresholds; category/region/source-class rules; filter-unverifiable behaviour; cursor stability; date validation; retry/backoff with fake clock; rate limiter; descriptor never contains secret values; v1/v2 result reader.
**Integration (offline, recorded fixtures):** RSS and search-API fakes through the real `create_app()`; partial failure keeps good results; all-unavailable state; cancellation; restart during search leaves a recoverable `FAILED/INTERRUPTED` run; history persists across restart; owner isolation.
**Contract:** JSON schema validation of v2 results and request bodies.
**Frontend:** node tests for the search form, filter state, each UI state (idle/loading/results/empty/partial/unavailable/error), escaping.
**Live (opt-in, skipped by default, reason printed):** one real provider run and one real RSS run; results recorded, not asserted on content.
**Regression:** full baselines — backend 287 passed/2 skipped, frontend 7, existing discovery tests unchanged.
**Browser acceptance:** rendered pass at 320, 390, 768, 1440 px with real results; screenshots stored under `docs/ui_screenshots/`.

## 13. Acceptance criteria

1. With D1/D2 provided, three real topics ("Today's Russia–Ukraine war update", "S-400 developments", "Indian defence procurement") each return results from at least one real provider, with publisher, URL, retrieval time and (where declared) publication date; no fabricated fields.
2. Category, region, source-class, date and language filters change results correctly, and unsupported/unverifiable cases are marked.
3. Provider failure and missing credentials are visible and do not remove successful results.
4. A search can be reopened after a server restart; rerun creates a new run.
5. Pagination is stable across pages; duplicates collapse with provenance preserved.
6. No content body is fetched or stored beyond the discovery metadata.
7. All baselines hold; new tests pass; unverified items listed.

## 14. Unverified until the phase runs

Which search vendor works for defence topics and its rate limits; real-world publication-date coverage of RSS feeds; Tamil-language source coverage; keyword category-rule accuracy.

## 15. Implementation steps (when authorized)

1. Taxonomy + config loader + provider descriptors (tests).
2. Result v2 + migration v8 + history API (tests).
3. Filters, pagination, dedup, retry/rate-limit (tests).
4. Real provider adapter behind the abstraction, with recorded fixtures.
5. Search UI states and history (node tests, then browser pass).
6. Opt-in live runs, acceptance report, update `project_status.md` and `REGISTRY.md`.
