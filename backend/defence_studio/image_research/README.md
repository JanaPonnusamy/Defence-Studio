# Image Research

Independent module for image search, acquisition, deduplication, rights metadata,
and human review. It does not require a content-research job. Optional
`research_topic_id` and `script_id` are stored as references only.

## API

- `GET /api/image-research/providers`
- `POST /api/image-research/jobs`
- `GET /api/image-research/jobs`
- `GET /api/image-research/jobs/{job_id}`
- `POST /api/image-research/jobs/{job_id}/run`
- `GET /api/image-research/jobs/{job_id}/candidates`
- `POST /api/image-research/candidates/{image_id}/review`
- `POST /api/image-research/jobs/{job_id}/reviews/bulk`
- `GET /api/image-research/jobs/{job_id}/duplicates`
- `GET /api/image-research/jobs/{job_id}/export`
- `GET /api/image-research/candidates/{image_id}/preview`

Accept is rejected unless `rights_status` is `VERIFIED`, commercial use is
`ALLOWED`, and the candidate is the preferred or unique image. Social-media
acquisition is `UNSUPPORTED`.

Tables are created by this module (`image_research_*`) and do not bump the
shared schema version while `database.py` has another session's uncommitted
migration.

Eligible counts exclude duplicates, broken downloads, irrelevant results,
unclear rights, and restricted licenses. A shortfall is reported when the
eligible count is below `minimum_unique` (default 30). `percent_complete` is
always null.
