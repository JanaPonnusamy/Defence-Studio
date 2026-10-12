# Changelog

## Unreleased (on top of 1.1.0), 2026-10-12: billable retry safety

Authorized behaviour change following the Phase 2 independent audit (`docs/PHASE_2_INDEPENDENT_AUDIT.md`).

### Changed
- **Billable generation requests are no longer retried automatically.** New setting `billable_generation_max_retries` (env `VIDEO_MODULE_BILLABLE_GENERATION_MAX_RETRIES`), default `0`. Previously a timed-out OpenAI request could be sent up to 3 times and billed each time.
- When billable retries are explicitly enabled, only failures where the provider demonstrably did not process the request are resent: connection never established (`ConnectError`, `ConnectTimeout`, `PoolTimeout`) and an explicit 429 rate-limit rejection. Read/write timeouts, connections dropped after sending, and 5xx responses are never resent.
- Non-billable providers (`placeholder-procedural`, `mock-local`) keep the previous bounded retry (`generation_max_retries`, default 2).

### Added
- Provider state `UNCERTAIN`: no result was received but the request may have been processed and billed (read/write timeout, connection dropped after sending). The generation `status` stays `FAILED`; it is never `SUCCEEDED`.
- For an UNCERTAIN billable request, an `UNKNOWN`-cost `CostRecord` with no asset is written, so the possible charge appears in the cost report (`unknown_cost_count`, `totals_complete: false`) and is counted by the budget guard at its pre-call estimate. It does not count as an AI image.
- Cost report field `uncertain_outcome_count`.
- `tests/test_timeout_no_retry.py` (16 tests, mocked HTTP): exactly one generation POST on every ambiguous failure even with retries enabled, no placeholder fallback after a timeout, UNKNOWN cost counted, honest timeout message, safe retries only when configured.

### Fixed
- An UNCERTAIN failure can no longer be turned into a placeholder by `allow_placeholder_fallback`.
- Timeout messages now say the request may have been processed and billed, instead of implying it failed before reaching the provider.

### Compatibility
- No schema migration: `UNCERTAIN` fits the existing `provider_state` column, and `uncertain_outcome_count` is an additive field. Error codes are unchanged (`PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`). Module version remains 1.1.0.
