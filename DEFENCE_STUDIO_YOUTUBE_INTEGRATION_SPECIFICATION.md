# Defence Studio — YouTube Channel Integration Specification

**Version:** 1.0 | **Date:** 2026-10-10
**Channel:** Defence Era | **Authorizing account:** `defenceerahq@gmail.com`
**Current handle:** `@DefenceEraHQ1` | **Preferred handle if available:** `@DefenceEraHQ`

> Detailed specification for the YouTube publishing module. This expands **Phase 9
> (Branding and publishing package)** of `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` and
> must obey the core-model rule: audit and reuse existing Defence Studio / QVault
> infrastructure, do not redesign, do not introduce a second architecture.

## 1. Objective

Build a reusable YouTube publishing module inside the existing Defence Studio
application. It must connect to the real channel using Google's OAuth 2.0 flow and
YouTube Data API v3 — not a Google password — and support metadata, video upload,
thumbnails, subtitles, scheduling, verification, retries, and persistent status.

Publishing pipeline:
1. Select a completed video asset.
2. Review title, description, tags, category, language, audience setting, visibility,
   thumbnail, captions, and schedule.
3. Validate files and metadata.
4. Upload video using resumable upload where supported.
5. Set thumbnail and upload caption tracks.
6. Apply visibility/schedule.
7. Verify remote status.
8. Persist all IDs, progress, attempts, events, and errors.
9. Display the same state after refresh, app restart, or opening the same server app
   from another authorized device.

Default to approval-gated publishing and private visibility. Never publish silently
merely because rendering finished.

## 2. Channel defaults

These are editable configuration defaults, not hard-coded assumptions:

| Field | Initial value |
|---|---|
| Display name | Defence Era |
| Authorization account | `defenceerahq@gmail.com` |
| Current handle | `@DefenceEraHQ1` |
| Preferred handle | `@DefenceEraHQ` |
| Country | India |
| Topics | Indian/global defence, Army, Navy, Air Force, defence technology, aerospace, military analysis, geopolitics |
| Tagline | `DEFENCE NEWS, DEEP ANALYSIS, EXPLAINED SIMPLY` |
| Default visibility | Private |
| Approval required | Yes |

The application must query the actual authorized channel and store its real channel
ID/title. Do not infer channel identity from the Gmail address or assume a handle is
available.

## 3. Non-negotiable project rules

- Read the project's master document and inspect the current repository before changing code.
- Preserve the current architecture, UI design system, completed phases, naming, and
  workflows. No redesign or unrelated refactoring.
- Reuse existing render, transcription/Whisper, SRT, TTS/audio, thumbnail, research,
  history, file storage, database, and job infrastructure where present.
- No mock data or fake successful upload states in production.
- Keep YouTube calls behind a backend service/provider; do not scatter API calls
  through UI components.
- Keep long-running work in a durable background worker, not in React state or an open
  browser tab.
- Persist stable job IDs, states, progress, attempts, timestamps, remote IDs, and audit events.
- Never store Google passwords or expose OAuth secrets/tokens to frontend code or logs.
- Do not claim upload, captions, thumbnail, schedule, or publication succeeded without
  API evidence and follow-up verification.
- Make the module reusable through configuration and service/API contracts.

## 4. Architecture and UI

### A. Channel settings

Add a YouTube section in the existing settings/navigation:
- Connection status: Not connected / Connected / Reauthorization required / Error.
- Authorized channel title, channel ID, handle if available, channel thumbnail.
- Connection/test/refresh/disconnect actions.
- Last successful check, last upload/sync, granted scopes.
- No secrets or tokens displayed.
- Warn if the connected channel differs from the expected channel.

### B. Publishing defaults

Allow editing: description template; optional title prefix; per-video tags; category;
video language. Default visibility, audience declaration, playlist. Caption languages;
thumbnail source; time zone. Approval policy, retry policy, source/reference review
requirements.

Keep channel-level keywords separate from per-video tags. Don't assume every YouTube
Studio setting is writable through the API.

### C. Publish dialog

Show video preview, title, description, tags, category, language, audience, visibility,
schedule/time zone, thumbnail preview, caption tracks, source checklist, validation
errors/warnings, and explicit final approval.

### D. Publishing queue

Show job, title, target channel, state, real progress, scheduled time, YouTube video
ID/link, last update, and errors. State-dependent actions: details, retry, resume,
cancel, edit schedule, open on YouTube, reauthorize, retry captions or thumbnail
independently.

### E. Job detail

Show a timestamped event timeline distinguishing render complete, upload transfer
complete, YouTube processing, thumbnail set, captions uploaded, schedule accepted,
publication verified. Do not collapse these into one misleading "Done" state.

### F. Background worker and recovery

The worker must continue when the user navigates away or closes the UI. On
application/worker restart, load unfinished jobs, check connection and upload-session
state, reconcile known YouTube video IDs, resume safe work, and surface jobs needing
user action. If the server itself is offline, transfer cannot continue during that
outage; preserve state and resume/reconcile when online again.

## 5. OAuth and security

Official guide: https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps

Use backend OAuth 2.0 web-server flow:
1. User clicks Connect YouTube.
2. Backend creates consent URL with configured client ID, exact redirect URI, minimal
   scopes, cryptographically random `state`, and offline access when background tasks need it.
3. Validate state on callback; exchange code server-side.
4. Fetch actual YouTube channel profile and persist channel ID/title.
5. Ask user to confirm it is Defence Era.
6. Encrypt refresh token at rest; restrict service access; refresh access token server-side.
7. On revoked/invalid credentials, pause dependent jobs and mark `REAUTH_REQUIRED`.

Security requirements:
- Never collect/store Gmail password.
- Never place client secrets/tokens in frontend, URLs, logs, analytics, crash reports,
  or source control.
- Use HTTPS in production; validate exact redirect URIs and OAuth state; protect callback
  against CSRF/replay.
- Keep secrets in environment configuration or existing secrets manager.
- Redact authorization headers and tokens from logs.
- Add role checks and audit connect/disconnect/upload/schedule actions.
- Disconnect must delete local credentials and revoke access where supported.
- Do not use a Google service account for an ordinary creator channel; service-account
  access is intended for eligible YouTube content partners/CMS use cases.
- Ensure the authorizing Google account has permission to manage the target channel.

Start with `https://www.googleapis.com/auth/youtube.upload` if sufficient. Caption and
broader metadata operations may require `https://www.googleapis.com/auth/youtube.force-ssl`.
Confirm method-specific scopes from current official docs and request only what the
selected features need.

## 6. Google Cloud setup

One-time developer/admin tasks:
1. Open https://console.cloud.google.com/
2. Create/select the Defence Studio project.
3. Enable YouTube Data API v3.
4. Configure OAuth consent screen and audience.
5. Add test users if app is in testing mode.
6. Create OAuth client credentials for the actual app type (normally Web application for
   a server-backed app).
7. Add exact authorized redirect URI(s).
8. Store client ID/secret server-side.
9. Test using `defenceerahq@gmail.com`.
10. Verify the returned channel ID/title before allowing publishing.

Use separate development/staging/production redirect URIs. Do not invent a redirect URI
before inspecting deployment. Check current Google OAuth verification, YouTube API quota,
and project restrictions before promising production availability.

## 7. Upload and metadata behavior

### Preflight validation

Validate that: video exists, is readable, and uses a supported format/size;
title/description satisfy current API limits; no unresolved placeholders remain in
description; category, language, tags, audience, visibility are valid; thumbnail and
caption files parse and use supported formats; scheduled time is future-facing and
interpreted in the configured time zone; user approved the final metadata; target channel
matches the saved channel ID; the same asset is not already being uploaded by another
active job.

Treat optional metadata as warnings unless the workflow explicitly requires it.

### Video upload

Use `videos.insert` with resumable upload where supported:
https://developers.google.com/youtube/v3/docs/videos/insert

Persist upload session state securely where supported, bytes transferred, progress
timestamps, and returned YouTube video ID. Progress must come from actual transfer
events. Handle timeout, network interruption, token refresh, quota/rate errors, worker
restart, and session expiration. Resume the existing session where possible. Before
starting a replacement upload, reconcile with YouTube to avoid duplicate videos. Do not
mark complete just because a request started.

### Metadata

Support current applicable `snippet`/`status` fields: title, description, tags, category,
language fields where supported, privacy status, audience settings, and scheduled
publication fields where permitted. Check the current API schema; not every Studio
setting is writable via API.

### Visibility and schedule

Support Private, Unlisted, Public, and scheduled publication where allowed. For
scheduling: validate future date/time and convert configured local time to the
API-required timestamp; use the required private/status configuration for scheduled
publication; read back the remote video and verify the schedule was accepted; show the
local scheduled time and timezone; support edit/cancel only as the API permits; if
eligibility or API restrictions block scheduling, explain the real error and leave the
video safe.

### Thumbnail

Use `thumbnails.set`: https://developers.google.com/youtube/v3/docs/thumbnails/set
Validate supported MIME type, file size and image requirements from current docs. Track
the result separately. Never silently substitute another image.

### Captions/subtitles

Use `captions.insert`: https://developers.google.com/youtube/v3/docs/captions/insert
Support English, Tamil, and configured languages. Reuse reviewed Whisper-generated SRT
when available; keep source transcript, reviewed version, and uploaded caption track as
separate records. Validate format and language. Verify track status when possible. The
captions API `sync` parameter is deprecated; don't depend on it. Caption failure must be
independently retryable without re-uploading the video. Caption API calls consume
meaningful quota.

### SEO metadata assistance

Suggest titles, concise first description lines, verified sources, relevant tags/hashtags,
chapters, language and subtitle availability. Allow user edits and per-series/language
templates. Never invent sources, quotes, statistics, dates, or defence claims. Don't stuff
unrelated keywords. Detect unresolved placeholders. Don't automatically assign the same
generic title to every video.

## 8. Durable job state machine

Adapt names to the existing project but preserve equivalent semantics:

`DRAFT`, `AWAITING_APPROVAL`, `QUEUED`, `VALIDATING`, `READY_TO_UPLOAD`, `UPLOADING`,
`UPLOAD_PAUSED`, `UPLOAD_RETRY_WAIT`, `UPLOADED_PRIVATE`, `THUMBNAIL_PENDING`,
`THUMBNAIL_UPLOADING`, `CAPTIONS_PENDING`, `CAPTIONS_UPLOADING`, `SCHEDULE_PENDING`,
`SCHEDULED`, `PUBLISHING`, `PUBLISHED`, `VERIFYING`, `COMPLETED`, `FAILED_RETRYABLE`,
`FAILED_FINAL`, `CANCEL_REQUESTED`, `CANCELLED`, `REAUTH_REQUIRED`.

Validate transitions server-side. Every transition creates a timestamped event with
previous/new state, actor/system, and safe error details.

On worker startup:
1. Load non-terminal jobs.
2. Check connection and resumable session.
3. Reconcile known YouTube video IDs against current remote status.
4. Resume safe work or mark retry/reauthorization required.
5. Never blindly create a second upload.
6. Show jobs requiring user action.

Frontend must reload state from backend/database; browser state is not the source of truth.

## 9. Suggested database entities

Adapt names/migrations to existing conventions and reuse equivalent tables if present.

### `youtube_connections`

`id`, `provider`, `display_name`, `google_account_email` (if reliably obtained),
`youtube_channel_id`, `youtube_channel_title`, `youtube_handle` (nullable/API-derived),
`encrypted_refresh_token`, `granted_scopes`, `connection_status`, `connected_by_user_id`,
`connected_at`, `last_checked_at`, safe error code, timestamps.

### `publishing_profiles`

`id`, `connection_id`, `name`, optional title prefix, description template, tags JSON,
category ID, language, default visibility, audience setting, playlist ID, caption languages
JSON, timezone, approval required, creator and timestamps.

### `publishing_jobs`

`id` (stable UUID), source project/asset ID, connection ID, target channel ID, YouTube
video ID, title, description, tags JSON, category, language, visibility, scheduled UTC
time, timezone, video/thumbnail file references, state, progress, bytes total/uploaded,
encrypted resumable-session reference if needed, attempts/max attempts, next retry, safe
error code/message, approver/time, creator, lifecycle timestamps.

### `publishing_caption_tracks`

`id`, job ID, language code, track name, transcript version ID, source file reference,
YouTube caption ID, state, draft flag, safe error code, timestamps.

### `publishing_job_events`

`id`, job ID, event type, previous/new state, safe message, metadata JSON (no secrets),
actor/system, created time.

Index state, retry time, connection ID, source asset ID, YouTube video ID and created
time as appropriate.

## 10. Errors, retries, quota

Handle consent denied, invalid state, revoked/expired token, missing refresh token, wrong
channel, insufficient scopes, redirect mismatch, quota exhaustion, rate limit, network
interruption, expired upload session, processing failure, invalid metadata, unsupported
media/caption, thumbnail rejection, caption rejection, schedule rejection, duplicate
ambiguity, worker restart and DB failure.

Use bounded exponential backoff with jitter and honor `Retry-After` when returned. Don't
retry permanent validation/permission errors forever. Keep actionable safe messages; never
expose tokens or secrets.

Quota reference: https://developers.google.com/youtube/v3/determine_quota_cost
Check current project allocation and method costs. Cache channel profile, avoid unnecessary
API calls, queue quota-consuming caption work, and avoid retry loops. Verify current API
project restrictions and YouTube terms/policies before release.

## 11. Acceptance tests

### OAuth
- Real intended account connects.
- Consent cancellation is handled.
- Invalid OAuth state rejected.
- Wrong channel detected before publishing.
- Tokens encrypted and absent from logs/client bundles.
- Disconnect/reauthorization works.

### Upload/recovery
- One real private video uploads and returns a real video ID.
- Transfer interruption resumes or reconciles.
- Worker restart restores state.
- Retries do not blindly create duplicates.
- Refresh/new device shows the same persisted state.
- Metadata matches the intended YouTube video.

### Thumbnail/captions
- Real thumbnail upload verified.
- English/Tamil captions uploaded where API/channel supports them.
- Caption failure retried without re-uploading video.
- Invalid caption file returns actionable error.

### Scheduling
- Future schedule accepted and verified.
- Past/invalid schedule rejected safely.
- Schedule changes/cancellation work where supported.
- UI uses API evidence for scheduled/published state.

### Security/reliability
- Unauthorized users cannot publish/disconnect.
- Retry is bounded; quota exhaustion does not loop.
- All state transitions are audited.
- Mocked tests are clearly labelled; never claim they are real API tests.

## 12. Phased implementation plan

### YT-01 — Inspect repository and plan
Read master document; inspect architecture, database/migrations, auth, permissions,
background worker, jobs/events, settings UI, file storage, rendering, Whisper/SRT,
TTS/audio and thumbnails. Identify reusable modules. Produce exact file-by-file plan,
dependencies and tests. **No code changes until plan is presented and approved.**

### YT-02 — OAuth and channel connection
Implement server-side OAuth, state validation, encrypted token storage, channel discovery,
connection test/disconnect/reauthorization. Exit: real Defence Era channel ID/title
displayed.

### YT-03 — Persistent queue and state machine
Reuse/add migrations, state transitions, event log, worker, startup recovery. Exit: state
survives UI refresh, app restart and second authorized device.

### YT-04 — Settings and preflight
Build channel settings, metadata templates, approval flow, validations, timezone-aware
scheduling UI. Exit: settings persist and a publish request validates without upload.

### YT-05 — Resumable video upload
Implement actual upload, progress, recovery and duplicate reconciliation. Exit: one real
private video upload verified by its video ID.

### YT-06 — Thumbnail and subtitles
Implement independently tracked thumbnail and caption uploads. Exit: real API results
verified; failures retry independently.

### YT-07 — Schedule and verification
Implement future scheduling, safe visibility, remote verification, edit/cancel where
supported. Exit: real schedule accepted and verified.

### YT-08 — Hardening and release
Test quota, revoked auth, permissions, restart, multiple devices, errors, audit, docs and
deployment config. Exit: critical acceptance tests pass.

## 13. Definition of Done

- OAuth connects the real Defence Era channel.
- Correct channel identity and ID shown.
- Secrets protected.
- Metadata reviewed and persisted.
- Real video upload with durable progress.
- Thumbnail/captions independently tracked.
- Scheduled publication verified.
- State survives refresh/restart/second device.
- Recovery avoids duplicate uploads.
- Errors actionable and audited.
- Existing project architecture reused; migrations/tests/docs included.
- At least one real end-to-end upload test completed; no mock-only success claims.

## 14. Official documentation

- OAuth web-server flow: https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps
- YouTube Data API: https://developers.google.com/youtube/v3
- Video upload: https://developers.google.com/youtube/v3/docs/videos/insert
- Video resource/status: https://developers.google.com/youtube/v3/docs/videos
- Captions insert: https://developers.google.com/youtube/v3/docs/captions/insert
- Captions resource: https://developers.google.com/youtube/v3/docs/captions
- Thumbnail set: https://developers.google.com/youtube/v3/docs/thumbnails/set
- Quota: https://developers.google.com/youtube/v3/determine_quota_cost
