# Fact-check and media-context engines

Two deterministic engines plus a thin job/API layer. They do not replace discovery, acquisition, review, or the video renderer.

## Engines

- `defence_studio.verification.claims.verify_claims` — normalize, cluster, independence, corroboration, verdicts.
- `defence_studio.verification.media.verify_media` — hash, average hash, PNG text, JPEG EXIF, optional frames.
- `defence_studio.verification.package.build_evidence_package` — versioned JSON and markdown.
- `defence_studio.verification.providers.capability_registry` — what is actually available.

Verdicts: `VERIFIED`, `PARTIALLY_SUPPORTED`, `CONFLICTING`, `NEEDS_REVIEW`, `UNSUPPORTED`, `OUTDATED`.

`UNSUPPORTED` means the collected evidence does not meet the support rule. It is not a finding that the claim is false. `falsity_assessment=DEBUNKED` is separate and is set only when an official primary source negates the claim and nothing independently supports it.

High-impact categories (`casualty`, `weapon`, `political`, `conflict`) stay `NEEDS_REVIEW` until a person overrides them. Package approval does not invent that override.

## Integration

Jobs use the existing `workflow_jobs` queue and one stage, `factcheck_run`. Schema v13 stores runs, package versions, overrides, and script bindings. A research job with no fact-check package keeps the existing evidence-approval script path. If a package exists, script generation waits for human approval of the latest version and cites only cleared claims.

Media reuse stays `RIGHTS_REVIEW_REQUIRED`. Context matching does not clear rights. Reverse image search, OCR, object detection, and ffmpeg frames report unavailable when the tool or credential is missing. They do not invent matches.

Paid script generation is unchanged: the existing budget guard still runs, and it is not called while a fact-check package is unapproved.

Phase 2 verification (2026-10-14): schema copies migrate 10→13 with existing jobs preserved. Hosted search (`DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL` + `_KEY`) is unset and makes no network call. Live RSS from `discovery_feeds.json` is the authorized search path. ffmpeg `showinfo` `pts_time` is recorded as `timestamp_ms` when printed; sample index is never treated as a timestamp. Media rights stay `RIGHTS_REVIEW_REQUIRED` unless AssetService already has `APPROVED` + `CLEAR`. The standalone Asset Rights Engine is not drop-in compatible.

## Reconciled status (2026-10-15)

- Readiness vocabulary in the UI: `READY`, `NOT_CONFIGURED`, `DEPENDENCY_MISSING`, `PROVIDER_UNAVAILABLE`, `LIVE_VERIFIED`, `TESTED_WITH_MOCKS`, `UNSUPPORTED`.
- Reverse-image: opt-in per media request (`live_reverse_search`); statuses `NOT_CONFIGURED` / `NO_MATCH_FOUND` / `MATCHES_FOUND` / `PROVIDER_UNAVAILABLE` / `INVALID_PROVIDER_RESPONSE`; no-match never means original. Needs an https gateway (`DEFENCE_STUDIO_REVERSE_IMAGE_URL`, optional `_KEY`; image bytes only with `_SEND_IMAGE=1`).
- OCR needs `tesseract` on PATH. Object detection has no model. Both outputs are untrusted evidence.
- Frame timestamps: only ffmpeg `pts_time` (matched by showinfo frame number) or a valid supplied non-negative number is a timestamp; everything else is `unverified`.
- Feeds containing DTD/entity declarations, or 200 responses that are not RSS/Atom, are feed errors.
- Evidence level: unit + mocked-integration + one real local ffmpeg run. No hosted provider was called.
