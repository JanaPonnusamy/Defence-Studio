# Defence Studio source provenance

F1 is a new standalone Defence Studio package. No QVault or Whisper Studio source files were copied, imported at runtime, edited, or linked into it. The following inspected QVault contracts and services informed compatible boundaries and pipeline naming; their behavior was not ported wholesale. Snapshot: QVault `develop`, HEAD `0c8146b53ec32d979d4a630b0c71c08af77fa8af`; checkout was already dirty at Phase 0. Hashes are SHA-256 of exact working-tree source files recorded during Phase 0, so combined with the commit ID they identify the audited inputs.

| Audited source component | SHA-256 |
|---|---|
| E:/QVault/backend/app/integrations/acquisition/dto.py | 5d3bcaf8a79c6fa83f9e89d70c4d843f4abe94b5e349482072371491cb339405 |
| E:/QVault/backend/app/services/acquisition_queue_service.py | fbd2296211232f6d6a2a1d99cfb8dcfa4b91f91689332284aebcd74c47695607 |
| E:/QVault/backend/app/services/knowledge_research_service.py | 46668d77ef26f584a22574c947b578b69796fbcca2a767055b61ff370a2e61ef |
| E:/QVault/backend/app/services/video_generation_service.py | 719533f6a4e2fa05d8073252d8e3ddeb94544847dae41260856245909d022050 |
| E:/QVault/backend/app/services/video_timeline_service.py | 5ac945aedd0fc5f3ee39610065cab2365ab256190e526b9fb2c2fc823da5b107 |
| E:/QVault/backend/app/integrations/tts/__init__.py | a53d45916b7cb8129d7440747762deb2b0791085aa9bc628c4253b5874f575da |
| E:/QVault/backend/app/integrations/tts/edge_provider.py | eb57641fe8480d92dd4e06cd65ed676c31d90412e437cc0f562ed2e4d0ef40a3 |
| E:/QVault/backend/app/integrations/acquisition/providers/education/discovery.py | 31bee146c45783797e22e992117828f66e026cf6f852884898d9981758862fdb |

## F2 component reuse and version fingerprints

F2 reuses the existing Defence Studio F1 job contract, owner-scoped service, SQLite bootstrap, API/auth boundary and job repository. It extends only the database schema/bootstrap, queue API, and read-only claim listing needed to integrate the worker. The F1 ordered stage definitions and payload/idempotency contracts were retained. No QVault or Whisper Studio implementation was copied, imported, or modified for F2. The following SHA-256 hashes identify the exact Defence Studio source components used by this F2 implementation (uppercase hexadecimal is equivalent):

| Component | Role | SHA-256 |
|---|---|---|
| `backend/defence_studio/contracts/jobs.py` | Reused locked job/stage contract | `B152F6EF27698347F1F4081575F5E9C1E442EC14E9978D154D9F98E9A5D52C33` |
| `backend/defence_studio/database.py` | F1 bootstrap adapted to schema v2 migration | `B5F7003EA3C85D943A88DDEF659FBD912C020C0A1C27ABF85B2EBB82AAF1BBBB` |
| `backend/defence_studio/services/jobs.py` | F1 owner/idempotency service integrated with claim fencing | `3691036EC0F59A75D0E7FE7436B984CDDCB6580808FBAB7D094015C4AAD84F6C` |
| `backend/defence_studio/api/app.py` | F1 auth/API boundary extended with queue and startup recovery | `A78AAE600F107BEE3D41E98AAEAC638B5CBFBC4B647B5EB812EF1BC505A082E9` |
| `backend/defence_studio/repositories/jobs.py` | F1 job repository extended for persisted claim visibility | `5D7154D8C0B231D52EC80C4A7F1A1BF9C897EA08A3D563FD2DBE1BF1EBBB6934` |
| `backend/defence_studio/services/worker.py` | New F2 worker/lease/retry engine | `ED2FB8B8CA21B9D9F8C2EB7C677507B1BC8E6F8DB78A3ACB08732646F2DEF59A` |

## F3 component reuse and version fingerprints

F3 adds content acquisition as a new, standalone `defence_studio.acquisition` package plus a `defence_studio.storage.artifacts` byte store. It executes only as a handler registered for the existing F2 `Worker`/`WorkerService` (no changes to `services/worker.py`, `services/jobs.py`, `database.py`, or the API) and consumes/produces only the existing F1/F2 artifact-reference shape. `config.py` gained one additive function, `storage_root()`, for the new byte-store location; no existing function changed behavior.

Three QVault acquisition files were inspected (hashes recorded above under Phase 0) to inform F3's design; **none were copied, imported, or executed**:

- `app/integrations/acquisition/dto.py` — informed `AcquiredSourceV1`'s shape (one uniform result type past the provider boundary), but QVault's `AcquisitionDocument` is a mutable dataclass with no SSRF/size/redirect guarantees; F3's contracts are frozen dataclasses with an explicit `MetadataStatus` tri-state QVault's DTO does not have.
- `app/integrations/acquisition/provider.py` — informed the `register()`/`get()`/registry pattern now in `defence_studio/acquisition/provider.py`'s `ProviderRegistry`; F3 adds `supports()`-based `auto` resolution and a `fallback` flag QVault's registry does not have, and drops QVault's `health()`/`create_job()`/`extract_metadata()` methods that are specific to its scan-job model.
- `app/integrations/acquisition/storage.py` — informed the deterministic-layout and short-filename pattern in `defence_studio/storage/artifacts.py`; QVault's `exam`/`year` path segments are replaced with the mandated `jobs/<job_id>/...` layout per the Defence Studio design, and F3 adds path-containment validation, atomic writes, and SHA-256-over-actual-bytes that QVault's `AcquisitionStorage.save()` does not perform.

Explicitly **not reused**: QVault's `app/integrations/gk_http.py` (uses `curl_cffi` browser-TLS impersonation to bypass site WAFs/anti-bot defenses, and performs no SSRF validation) and `app/integrations/ytdlp.py`'s `YtDlp.download_video` (downloads the full video stream, and its `CookieSource` sends authenticated session cookies). Both are incompatible with F3's "never bypass access controls" and SSRF-guard requirements, so F3 implements its own `httpx`-based `GuardedFetcher` (`defence_studio/acquisition/http_client.py`) and a subtitle-only yt-dlp call (`defence_studio/acquisition/providers/youtube.py`) that sends no cookies and never fetches the video stream.

New F3 source components and their SHA-256 (uppercase hex is equivalent):

| Component | Role | SHA-256 |
|---|---|---|
| `backend/defence_studio/contracts/acquisition.py` | Acquisition request/result contracts, `MetadataStatus` | `781ed9a7d9be94b3843055dee8050b66cd23c8623c7420113ada5aa73f664669` |
| `backend/defence_studio/storage/artifacts.py` | Path-safe, atomic artifact byte store | `02a88a946eb015a65571bd3ea679887dafbbe99e830cb4752edec2b1d8e4ab52` |
| `backend/defence_studio/acquisition/url_guard.py` | SSRF validation (scheme/credentials/resolved-address blocking) | `06d3820a205102914153f1bd85826156c63b818ec55f6254d308d580a282f480` |
| `backend/defence_studio/acquisition/http_client.py` | Guarded fetcher: redirect re-validation, size/time bounds | `cef3fec70938a2f921b79270a7f79231f7944fbab18833fdc8ed5dc3d9aff43b` |
| `backend/defence_studio/acquisition/provider.py` | Provider interface + registry (adapted from QVault's pattern) | `3a6c0a3f2f4b93b198606c83dc4bdfab11c2a6a8436f0ac94b56f2e29ca97950` |
| `backend/defence_studio/acquisition/providers/http_page.py` | Generic HTTP page/PDF provider | `30ba87376f36ca5df8058e57e676cc137a3d0c7c3889fd51542fabe676921735` |
| `backend/defence_studio/acquisition/providers/youtube.py` | YouTube subtitle-only provider (injectable yt-dlp backend) | `21ca0ab9ee9e7777378d7d922c6f37e5cd3f4331a22ed428e9d7f1ecf13230f2` |
| `backend/defence_studio/acquisition/service.py` | Orchestration: clamping, dedup, idempotent reuse, partial success | `df4b002fc0ebc27661141d8145f7bf1e36b6cd947ee0534064b59eb351ebafe3` |
| `backend/defence_studio/acquisition/handler.py` | Retry-safe F2 worker stage handler binding | `0930b5adcdfba8f82b7cdb67d37da6718982feb3ab1d8f6bcf20361fde162f49` |
| `backend/defence_studio/config.py` | Added `storage_root()` (additive; existing functions unchanged) | `545f653d757829cc4cad1eb8757e24008324d7269fbc8341681a9e75d91968ae` |

## F4 component reuse and version fingerprints

F4 adds a new, standalone `defence_studio.analysis` package plus `defence_studio.contracts.analysis` and a new handler-binding function in `defence_studio.services.analysis`. It executes only as a handler registered for the existing F2 `Worker`/`WorkerService` (no changes to `services/worker.py`, `services/jobs.py`, or `database.py`) and reads/writes only through the existing F1/F2 artifact-reference shape and the existing F3 `ArtifactStore`. **No QVault or Whisper Studio source was inspected, copied, imported, or executed for F4** — every module is new, stdlib-only (no new third-party dependency), and deterministic; no AI/NLP model or paid API is called. `defence_studio/storage/artifacts.py` gained one additive method, `read_bytes()` (bounded-size byte read with the same path-containment guard as `read_text`/`write_bytes`); no existing method's behavior changed, and its SHA-256 below is unchanged from the F3 table above because the method was already present in this file when F3's hash was recorded.

New F4 source components and their SHA-256:

| Component | Role | SHA-256 |
|---|---|---|
| `backend/defence_studio/contracts/analysis.py` | Analysis request/result/claim/evidence contracts | `4d22400b8ff024fa9beb26831c97e9d08dada545c654aaae61696374bf61b002` |
| `backend/defence_studio/analysis/normalize.py` | HTML/subtitle/plain-text normalization, script-based language guess | `7cf21b7d5ccede12ccd4ec9d794299974dc928bc31851debba3dd9cf47da1451` |
| `backend/defence_studio/analysis/dedup.py` | Exact/identical-content/near-duplicate source grouping | `81ea39dd96f52b131020563275746a808242eb42498d9df31e1100f56de7d265` |
| `backend/defence_studio/analysis/claims.py` | Deterministic verbatim claim/passage extraction | `8440a4b9c9489f4956cd143a292731fbd5be38aac7893462d253f6ab03e46e37` |
| `backend/defence_studio/analysis/relationships.py` | Claim grouping and cross-source conflict flagging | `b1b9cec4f3e85ab86fbe35c0c5d039132e19087f15f7285d58de7fe10babeaad` |
| `backend/defence_studio/analysis/ai_schema.py` | Strict AI-output validation seam (no provider wired) | `370f74ff75437187b6b69423d1b55319d1a15f3ad7dfbe8100b5a802b56b52b6` |
| `backend/defence_studio/services/analysis.py` | Orchestration service + retry-safe F2 worker handler binding | `80d144550a9e998bd80a8f3f63137d364700a3889b3cda2f7b8fea0fcd5a49bd` |

## F5 component reuse and version fingerprints

F5 adds a new `defence_studio.contracts.review`/`repositories.review`/`services.review` module set plus a new, framework-free `frontend/` static app. **No QVault or Whisper Studio source was inspected, copied, imported, or executed for F5.** It reads F3's `acquisition_manifest` and F4's `analysis_result` artifacts read-only through the existing `ArtifactStore` and reads job ownership through the existing `JobRepository`/`JobService` — no parallel project/job/source/claim identity was introduced. Three existing files were extended additively, not rewritten: `database.py` (schema v2→v3 migration, `_upgrade_v2_to_v3`, additive only — no F1–F4 table/column/row touched), `api/app.py` (new `/review/...` routes, a `ReviewError` exception handler, an optional `storage_root_path` parameter on `create_app()` defaulting to the existing `config.storage_root()`, and a `StaticFiles` mount for `frontend/` registered after every API route), and `tests/test_worker_engine.py` (two literal `SCHEMA_VERSION`/`schema_info` assertions updated from `2` to `3` to track the real migrated version -- the migration behavior under test is unchanged).

New F5 source components and their SHA-256:

| Component | Role | SHA-256 |
|---|---|---|
| `backend/defence_studio/contracts/review.py` | Review decision/subject/history contracts | `459135aaae8afa10bbb43177c322f99570d6bbcf1b09d42aade2cf5db257ec75` |
| `backend/defence_studio/repositories/review.py` | SQL access over the review/history tables | `38c98b50dd0ef770adf1c556c8383940a013b071dca4d172565de23da014b4cc` |
| `backend/defence_studio/services/review.py` | Read-models + decision service (stale/concurrency/idempotency) | `b5fb0945ef3c2a8addabc88b5fa2a82f2d72f30f849154a9d14c72b5339fdbd5` |

Modified existing files and their post-change SHA-256:

| Component | Change | SHA-256 |
|---|---|---|
| `backend/defence_studio/database.py` | Added schema v3 migration (additive) | `438a801c78ffd490f9cb9e55aad33fd6dbe490e504e9585668d21aa77f9e325f` |
| `backend/defence_studio/api/app.py` | Added `/review/...` routes, `ReviewError` handler, `storage_root_path` param, frontend static mount | `f8b8d3594026adf5eea3455151a67c50c04ad805e3480a7a62c91f41b5630a12` |

`frontend/` is wholly new, framework-free HTML/CSS/ES-module code (no third-party JS dependency, no build step). It is not a backend Python module, so it falls outside this file's per-module hash convention; see `CONTRACTS.md`'s "F5 evidence review contract" section for its role and `component_registry/REGISTRY.md` for its registered primitives.

## Phase 1A feed discovery component reuse

Phase 1A adds a new, standalone `defence_studio.discovery` package (`providers.py`, `ranking.py`, `service.py`, `handler.py`) plus `defence_studio.contracts.discovery` and `defence_studio.services.discovery`. **No QVault or Whisper Studio source was inspected, copied, imported, or executed for Phase 1A.** It reuses three existing Defence Studio components rather than re-implementing them: F3's `defence_studio.acquisition.url_guard.normalize_url`/`UrlGuard` (canonical-URL dedup key and SSRF validation) and `defence_studio.acquisition.http_client.GuardedFetcher` (every provider's only fetch path), and F3's `MetadataStatus` tri-state (`defence_studio.contracts.acquisition`) for declared-candidate-metadata provenance. It executes only as a handler registered for the existing F2 `Worker`/`WorkerService` (no change to `services/worker.py`) and produces an `acquisition_request` artifact F3's existing `request_from_dict` parses unmodified -- no new or diverging contract on the F3 side.

One existing file gained a narrowly-scoped additive method: `defence_studio/services/jobs.py`'s `add_artifact` gained an internal `_require_started` parameter (default `True`, preserving every existing call site's behavior unchanged) and a new public wrapper, `add_pretransition_artifact`, that allows an owner to attach an input artifact to a stage that has not started yet -- but only while the job is still `DRAFT`, before any worker claim exists. This is needed because `application_foundation` (the locked stage before `news_feed_and_topic_discovery`) has no handler of its own, so there is no other point at which an owner-submitted discovery request could become a predecessor-stage artifact for the discovery stage to consume. `defence_studio/api/app.py` gained the `/discovery/...` routes and a `DiscoveryError` exception handler, following the same pattern F5's `/review/...` routes and `ReviewError` handler established.

New Phase 1A source components and their SHA-256:

| Component | Role | SHA-256 |
|---|---|---|
| `backend/defence_studio/contracts/discovery.py` | Discovery request/result/candidate/ranking contracts | `acc115eb764e96358a3eb84dc1f09750f7e39994d4a3c621130859e7e2076cb5` |
| `backend/defence_studio/discovery/providers.py` | `rss_feed` + `configured_search_api` providers, registry | `100c369e952e4cdfbff5af69a00ff19184c400c440156b98f5af7eb351abfade` |
| `backend/defence_studio/discovery/ranking.py` | Deterministic, explainable candidate scoring | `2c8268d45805d40c271340778394a17aea2f210318052f64d4a23c6ad30838e2` |
| `backend/defence_studio/discovery/service.py` | Orchestration: clamping, per-provider isolation, canonical dedup | `b7571be4e6330bed0c71b9467eaa9b8bca6b8ed8a4529909efa96755a4a9ca83` |
| `backend/defence_studio/discovery/handler.py` | Retry-safe F2 worker stage handler binding | `bd644a38668d3bc500f5c8020d1d7311fe643718f2d993c926502812a3355baf` |
| `backend/defence_studio/services/discovery.py` | API-facing service: request/browse/select, F3 handoff | `9fc5c42b0b2b2508953c2e669a189654828fc927aaffd3fe8c9e97a394790caf` |

Modified existing files and their post-change SHA-256:

| Component | Change | SHA-256 |
|---|---|---|
| `backend/defence_studio/services/jobs.py` | Added `add_pretransition_artifact` (additive; `add_artifact`'s default behavior unchanged) | `2325d38fdee7b49dba66243ac79ae0f007ad720de071f4f92af139d5f2c62f21` |
| `backend/defence_studio/api/app.py` | Added `/discovery/...` routes and a `DiscoveryError` exception handler | `387403e00e66873f535f538dd660d94b4df61f29202fc052599c3eaccd742799` |

## Phase 3 source-grounded script generation component reuse

Phase 3 reuses existing Defence Studio capabilities rather than adding parallel infrastructure. `services/script.py::ScriptService` subclasses the Phase 2 `EvidenceService` (itself a subclass of F5's `ReviewService`), reusing its job-ownership checks, `ArtifactStore` byte access, JSON-artifact integrity verification, and evidence-package assembly/approval-ledger logic unchanged. The OpenAI provider reuses the project's existing testable-HTTP-seam convention (the `client_factory` pattern from F3's `acquisition/http_client.py`) rather than adding the `openai` SDK — no new runtime dependency (`httpx` is already required). The approval/audit persistence reuses the F5/Phase-2 immutable-ledger + append-only-history pattern. No QVault or Whisper Studio source was consulted or reused for Phase 3.

New Phase 3 source components and their SHA-256:

| Component | Role | SHA-256 |
|---|---|---|
| `backend/defence_studio/contracts/script.py` | Script request/response/revision/validation/approval contracts + deterministic word-count/duration helpers (extends the pre-existing offline boundary) | `a5bde7b55cc4d704971ec201b40bdc89e53bfc158ba820cd559e73d30df3e8ea` |
| `backend/defence_studio/providers/__init__.py` | Provider package marker | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `backend/defence_studio/providers/openai_text.py` | Bounded, retrying OpenAI Chat-Completions client behind a testable `client_factory` seam; env-based key; INR cost table | `463d2a61556f1f86c4e5e0b81dcfc814d46790b63fc140b3c0bbe45f4319435d` |
| `backend/defence_studio/services/script.py` | `ScriptService`: generation, revision persistence, review/approval lifecycle, export | `e8dbcb48b1f2fc9f958b85acb37b830b1704ac996b924adc7482309a9a45e955` |
| `backend/defence_studio/services/script_validation.py` | Deterministic evidence-grounded structural validation | `2ae9d733dd336f6fcee0641078544b09a119d8e1fd4f61a4b74bf85c47e18e09` |
| `backend/defence_studio/services/script_prompt.py` | Deterministic, source-grounded prompt construction (terminology preservation, approved-claims-only) | `425ade2be85be98ee17b0c2df0700c8a608862224aaf49f2e288cceefef2a999` |

Modified existing files and their post-change SHA-256 (all additive; no prior behavior removed):

| Component | Change | SHA-256 |
|---|---|---|
| `backend/defence_studio/database.py` | Added schema v5 migration `_upgrade_v4_to_v5` (`script_revisions` + `script_review_history`); accept prior versions {1,2,3,4,5} | `833938c6aebe6aff3f08d24a94b46be0d946840d236fe149bd6e409a458ead2d` |
| `backend/defence_studio/api/app.py` | Added the `/script/...` routes and wired `ScriptService`; Phase 3 errors reuse the existing `ReviewError` handler | `32f91abdd1b22df5bcef3cf3f778653e7bd35ec004426fb53f15c45f9b3454c1` |

New frontend components: `frontend/js/views/script-view.js` (Script tab: generation controls, narration/evidence/validation viewer, revision history, review feedback, approval, export) reuses the existing text-field (UI-01), confirm-dialog (UI-04), and toast primitives; `frontend/js/api.js`, `frontend/js/app.js`, and `frontend/styles/main.css` gained additive Script-tab wiring. (Frontend fingerprints are omitted here consistent with the prior phases' provenance tables, which fingerprint backend source only.)

Note: `contracts/script.py`'s SHA-256 reflects the file at Phase 3 completion; a concurrent session was observed editing it (adding optional `tone`/`audience`/`depth` fields to `ScriptRequestV1`), so recompute from disk if reconciling against a later edit.
