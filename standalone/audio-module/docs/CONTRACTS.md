# Integration Contract (spec section 23)

External applications may consume, via the public HTTP API only:

- Generated Audio (`GET /generated/{id}`, `GET /generated/{id}/file`)
- Audio Metadata (`GET /audio/{id}`)
- Duration (present on `AudioAsset`/`GeneratedAudio` responses)
- Word/Sentence Timing (`GET /alignment/{id}`)
- Performance Metadata (`GET /profiles/{id}`, `GET /profiles/plan/{plan_id}`)
- Job Status (`GET /jobs/{id}`)

External applications must **not** depend on:

- internal analyzer classes (`app.pipeline.*` Python objects)
- internal database tables/schema (`app.db.models`)
- internal storage paths (`storage/**` layout may change)
- provider implementation details of any TTS adapter
- any private (`_`-prefixed) module function

All responses use stable string identifiers (`reference_id`, `audio_id`,
`job_id`, etc.) as primary identity, never filenames.

## Error model

Every error is `{"error": {"code": str, "message": str, "details": {}}}`
with a matching HTTP status. Stable `code` values currently used:
`not_found`, `validation_failed`, `unsupported_media`,
`rights_metadata_required`, `job_failed`, `internal_error`.

## Job statuses

`QUEUED -> RUNNING -> SUCCEEDED|FAILED` (or `CANCELLED`, reserved for
future use; cancellation is not yet implemented in this build).
