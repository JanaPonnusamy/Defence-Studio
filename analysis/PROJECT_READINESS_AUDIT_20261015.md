# Defence Studio readiness audit — 15 October 2026

## Verdict

The project has a functioning local English production chain and useful standalone components. It is **not ready for unattended Tamil production, independent automatic fact verification, or a reproducible fresh-machine installation**. This machine lacks both an exposed Tamil SAPI voice and the Tamil-capable font required by the relevant tests. Existing passing tests do not close those gaps.

This was an analysis and verification pass, not an implementation pass. Product source was not changed. Existing databases, credentials, and research decisions were not deliberately modified; tests and newly started services used temporary storage. No commit, push, paid generation, or publishing was performed.

## Component readiness

| Area | Actual capability | Readiness |
|---|---|---|
| Core FastAPI application | Discovery, acquisition, claim extraction, review, evidence packages, scripts, scene plans, production integration, QC, packaging, credentials, publishing workflow | Implemented; installation defects and factual-integrity defect below prevent an unrestricted ready claim |
| Static frontend | Dashboard, discovery, sources/evidence/script review, production and settings views | 30 Node tests pass; this audit did not repeat rendered-browser acceptance |
| Standalone Audio Module | Reference ingestion, acoustic analysis, performance planning, local OS TTS, approximate alignment | 31 tests pass; real English integration passes. Tamil and crash recovery are not ready |
| Standalone Video Visual Module | Asset import, graphics, previews, renderer-neutral timed shot planning | 206 pass / 2 paid-provider checks skipped. Planning is not rendering; Tamil intent matching is limited |
| Standalone Video Renderer | Still-image timeline + WAV to H.264/AAC MP4, output probing and full decoding | 117 pass / 1 Windows symlink check skipped; real core integration passes. Narrow R1 renderer scope |
| Standalone fact-check service | No independently packaged service found | Not present. Core evidence-assessment and human-review services exist |
| Asset & Rights Engine | Metadata-based rights, review history, assignment readiness, SQLite validation/snapshots | 140 unittest tests pass; standalone library, not a licence-verification service; no core import found |
| Defence Align | Transcript-preserving alignment contracts, gated real model adapter, synthetic test adapter | 71 pass / 5 Torch-dependent tests skipped. Real alignment not verified in this audit; editable install fails |
| Automation | Separate NDF governance/version/release tooling | Inspected at documentation/configuration level; commands that mutate releases or Git were not run |

## Findings, ordered by impact

### 1. High — rejected evidence can count as corroboration

`backend/defence_studio/services/script_integrity.py:56–68` counts every claim in a matching claim group, regardless of its human review decision, source review decision, eligibility, or staleness. At line 111 that count is used to satisfy the two-source requirement for `VERIFIED_FACT`.

Reproduced using two sources and two grouped claims: only `approved` was in `eligible_claim_ids`; the second claim and its source were explicitly `REJECTED`. `corroboration_count(package, 'approved')` returned **2**. `integrity_issues` emitted a missing-date warning but **no `FACT_NOT_CORROBORATED` error**.

The evidence package retains rejected claims for the audit trail, so checking only group membership is insufficient. Count only current eligible claims backed by current approved sources, then deduplicate those sources. Add a regression covering rejected, pending, stale, and duplicate evidence. Textual similarity and distinct source IDs still do not prove editorial independence or factual truth.

### 2. High — documented backend installation fails

Fresh `pip install -e './backend[test]'` failed with:

```text
Multiple top-level packages discovered in a flat-layout: ['config', 'defence_studio']
```

`backend/pyproject.toml` needs explicit package discovery/build configuration. In addition, `cryptography` is used by credentials and YouTube services and imported by three test files but is absent from runtime and test dependency declarations. A clean audit environment therefore stopped collection until it was installed manually.

The initial bare backend test command also fails before tests run: `tests/conftest.py` imports `api.app` before setting its test secret, while `api.app` constructs a module-level app that requires the secret. Exporting a temporary test secret permits collection. Configure tests before importing the application, or remove application-construction side effects from test imports.

### 3. High for Tamil use — standalone TTS does not enforce voice/language compatibility

`standalone/audio-module/app/tts/local_pyttsx3.py:52–73` falls back to the OS default when the supplied voice ID is unknown and copies `request.language` into the result without checking the selected voice's language.

SAPI inventory on this machine contains only Microsoft Hazel (English UK, `809`) and Microsoft Zira (English US, `409`). No Tamil SAPI voice was exposed.

The core's `AudioService.check_provider_policy` rejects Tamil under its default `local-pyttsx3:en` policy. Direct standalone calls do not have that protection, and changing a declaration is not proof that a voice supports Tamil. Reject unknown voice IDs and unsupported languages at the provider boundary; report the actual chosen voice and tested languages.

### 4. Medium — standalone Audio Module does not recover interrupted jobs

`standalone/audio-module/app/jobs/queue.py` uses an in-process executor. Startup in `app/main.py` initializes storage/database but performs no recovery sweep. A process crash can leave `RUNNING` records permanently stuck; persisted queued rows are not automatically resubmitted either. The core's recovery of its own integration rows does not repair the standalone module's queue.

Implement interrupted-job reconciliation and a defined retry path before unattended operation. Also serialize or isolate OS TTS work if concurrent synthesis is supported; concurrency was not stress-tested here.

### 5. Medium — Defence Align installation fails despite passing source-tree tests

Fresh editable installation fails with:

```text
Multiple top-level packages discovered in a flat-layout: ['data', 'samples', 'align_engine']
```

`components/defence-align/pyproject.toml` declares a build backend but lacks explicit package selection. Running tests from its directory bypasses this packaging problem. Configure discovery to include `align_engine` only, then verify wheel installation and imports outside the source tree.

### 6. Medium — standalone rendering does not equal finished-video production

The standalone renderer deliberately lacks subtitle burn-in, Tamil/non-Latin overlays, branding, music, video clips, thumbnails, and a UI. Unsupported non-Latin overlays produce a notice/warning. Core integration adds subtitles and later packaging/branding outside this component.

The standalone renderer also does not create a credits card from `timeline.attributions`; source captions and rights-warning overlays are rendered. Required credits must therefore be handled explicitly before distribution. A successful codec/decode QC report is not proof that all editorial or attribution requirements are met.

### 7. Medium — fact-checking is a core-coupled evidence workflow

`backend/defence_studio/services/assessment.py` checks exact source offsets/text, stored conflict relationships, and risk indicators. It deliberately never returns `SUPPORTED`; possible outputs include `UNSUPPORTED`, `CONTRADICTED`, `NEEDS_REVIEW`, and `PARTIALLY_SUPPORTED`. Matching text proves extraction integrity, not truth. The risk check counts available normalized sources, not independent corroboration of that specific claim.

`ReviewService` pins human decisions to artifact hashes and revisions. `EvidenceService` gates use on approved sources/claims and validates exact passages. `script_validation.py` and `script_integrity.py` enforce citation/statement/number rules. These are valuable safeguards, but they do not establish semantic entailment of every narration sentence or independent real-world truth.

There is no standalone fact-check package with its own server, persistence boundary, dependencies, and consumer contract. If this is required, first fix finding 1, then extract a versioned boundary rather than labelling the current internal services standalone.

### 8. Medium — readiness documentation mixes historical and current scope

`project_status.md` contains old 'not integrated' phase rows alongside later integration records. Module READMEs still contain old `D:` paths; the rights-engine README points at its previous external location. Alignment's historical 76-pass claim differs from this environment's **71 passed / 5 skipped**, because real-adapter tests need Torch.

Keep standalone capabilities distinct from features supplied by core post-processing, and publish one current acceptance matrix that separates source-tested, installed, integrated, live-provider-verified, and human-reviewed status.

## Fresh verification evidence

Environment: Windows, Python 3.12.10, Node 24.15.0, system FFmpeg/FFprobe available at `D:/ffmpeg/bin`. An isolated `.audit-venv` was created for module dependencies; `pip check` passed after installation. Exact resolved packages are recorded in `analysis/readiness-dependencies-20261015.txt`.

| Check | Result |
|---|---|
| Frontend `node --test tests/*.test.mjs` | 30 passed |
| Audio `python -m pytest -q -ra` | 31 passed |
| Visual `python -m pytest -q -ra` | 206 passed / 2 skipped: paid provider opt-ins |
| Renderer `python -m pytest -q -ra` | 117 passed / 1 skipped: symlink privilege |
| Defence Align `python -m pytest -q -ra` | 71 passed / 5 skipped: Torch missing |
| Rights `python -m unittest discover -s tests -q` | 140 tests, OK |
| Three real HTTP integration checks | 3 passed: English audio, visual planning, complete audio/visual/render/subtitle chain |
| Live external HTTP acquisition | 1 passed against the test's default `https://example.com/` source; establishes this acquisition path, not news extraction quality |
| Backend regression in existing backend environment | **428 passed / 4 skipped**, 744.13 seconds; skips: live HTTP opt-in, live YouTube opt-in, Tamil thumbnail font, Tamil subtitle font. Live HTTP passed separately |

The local integration services were started against new temporary storage on loopback ports 8001, 8765, and 8780. The integration test verifies a 1280×720 H.264/AAC output, measured-duration agreement, subtitle cues, and zero decode errors. This proves the tested local English chain, not every language, asset type, duration, or external provider.

Individual logs are `analysis/readiness-{audio,visual,renderer,align,rights,real-chain}-20261015.log`. Logs are locally retained and ignored by Git. The frontend result was captured in the tool output.

A preliminary backend run in the shared module audit environment was discontinued after reproducing dependency incompatibility: the Visual Module pins FastAPI 0.115.6 / Starlette 0.41.3, whose `TestClient` rejects the backend tests' `client=` argument. A targeted reproduction is in `analysis/readiness-shared-environment-20261015.log`. This is not recorded as a product authentication failure. The final backend regression uses the existing backend Python environment (FastAPI 0.141.1 / Starlette 1.7.0), with an exported test secret and temporary default database/storage paths. Module services remain separate processes. The audit virtualenv is excluded only in local `.git/info/exclude`; project ignore rules were not changed.

## Remaining acceptance gates

1. Fix rejected-evidence corroboration and the two packaging failures; declare `cryptography` and make bare test startup reliable.
2. Verify each component in its own reproducible environment, including wheel/clean-checkout installation. Shared dependency environments are not a supported deployment assumption: the Visual Module pins an older FastAPI/Starlette stack.
3. Add a real Tamil-capable TTS provider/voice and a discoverable Tamil-capable font, enforce voice capability checks in the standalone API, and have Tamil narration and script fidelity reviewed by a Tamil speaker. The two font-dependent backend tests skipped on this machine; historical Tamil shaping claims do not establish current readiness.
4. Provision an appropriate real alignment backend/model if accurate word timing is required, and verify it on real English/Tamil speech. The audio module's proportional/VAD timings remain approximate.
5. Verify live text generation, optional image generation, YouTube caption acquisition and OAuth/private upload separately with configured providers. Those were not exercised in this audit. Basic live external HTTP acquisition passed separately.
6. Add standalone audio recovery; verify stop/restart and concurrent-job behavior before unattended use.
7. Repeat populated browser acceptance and a representative complete episode with actual approved assets, credits, Tamil narration, QC approval, and final packaging. Historical screenshots and a synthetic/offline test suite do not close this acceptance gate.

## Audit boundaries

No exhaustive security assessment, dependency-vulnerability scan, licensing determination, production load test, or human editorial review was performed. Rights and model-licensing notes in repository documentation remain operator prerequisites; this audit makes no independent legal determination. No external factual assertions about news events were researched: the fact-check assessment concerns the project's implementation.
