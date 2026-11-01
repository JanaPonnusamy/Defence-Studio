# Storage audit — 2026-11-01 (Agent A, read-only)

Scope: `D:\jana soft\DefenceEra` only. `E:\Jana Soft\DefenceStudio` was **not audited**: drive `E:` does not exist on this machine.
Method: `os.walk` + `stat` + streamed SHA-256 (scratch script, `python -I`), and a read-only SQLite connection (`mode=ro`) to
`data/defence_studio.sqlite3`. **Nothing was deleted, moved or modified.** 20 media files under `standalone/audio-module/storage`
were locked by a running process and could not be hashed, so the audio module is live.

## 1. Inventory

`path | module | file_type | count | total_size | lifecycle | cleanup_safety`

| path | module | file_type | count | total_size | lifecycle | cleanup_safety |
|---|---|---|---:|---:|---|---|
| `data/storage/jobs/<job>/render/` | core render (RM-5/7) | .mp4 (+ -subtitled.mp4) | 99 files | 796.6 MB | per-revision outputs; superseded revisions kept | UNSAFE until registry knows which revision is approved/packaged |
| `data/storage/jobs/<job>/audio/` | core audio (RM-4) | .wav | 30 | 44.5 MB | per-scene narration, hash-verified | UNSAFE (referenced by audio sets/packages) |
| `data/storage/jobs/<job>/package/` | core package (RM-7) | .mp4/.jpg/.json | 7 | 33.0 MB | branded final package | PRESERVE (final video) |
| `data/storage/jobs/<job>/assets/` | core asset service | .jpg/.png | 155 | 21.0 MB | ingested rights-tracked images | PRESERVE if accepted; else review |
| `data/storage/jobs/<job>/research/` | discovery/acquisition/analysis | .json/.html/.txt | 131 | 8.3 MB | raw HTML + extraction + analysis | raw HTML = TEMP (7 d) **only if** no retained report/evidence package cites it |
| `data/storage/jobs/<job>/evidence/` | evidence package | .json | 22 | 3.0 MB | approved evidence | PRESERVE |
| `data/storage/jobs/<job>/scripts/` | script service | .json | 45 | 2.2 MB | revisions | PRESERVE approved; drafts small |
| `data/storage/jobs/<job>/{visual,audio-sets,renders,scenes,qc,discovery,packages,approvals}/` | various | .json | 58 | 1.3 MB | manifests/bindings | PRESERVE (tiny, referenced) |
| `standalone/video-renderer/storage/renders/` | Video Renderer | .mp4 | 8 | 341.8 MB | module work output | DUPLICATE of core copies (see §2); deletion needs module + core agreement |
| `standalone/audio-module/storage/{audio,generated}/` | Audio Module | .wav | 102 | 168.1 MB | module TTS output | module owns; partly duplicated in core |
| `standalone/audio-module/storage/{waveforms,analysis,alignment}/` | Audio Module | .json | 99 | 2.7 MB | analysis | keep |
| `standalone/video-visual-module/storage/{assets,generated,plans}/` | Visual Module | .jpg/.png/.json | 98 | 61.5 MB | asset cache + plans | module cache; duplicated in core |
| `standalone/*/storage/*.db*` | module DBs | .db/.db-wal | 5 | ~8 MB | module state | NEVER clean |
| `analysis/su57_tamil_sample_20261029/` | manual sample (untracked) | .mp4/.wav/.png/.jpg | ~44 | ~146 MB | ad-hoc review sample | owner decision; not managed storage |
| `analysis/phase0-baseline/` etc. | audit evidence | .db/.js/.json | ~50 | ~1.5 MB | historical evidence | PRESERVE |
| `data/defence_studio.sqlite3*`, `*.bak-20261028-*`, `local-backup.sqlite3` | core DB + 2 backups | .sqlite3 | 7 | (not summed) | live DB + manual backups | NEVER clean automatically |
| `data/acceptance/` | acceptance isolated tree | mixed | — | 1.6 MB | disposable acceptance data | SAFE only through acceptance tooling |
| `.claude/worktrees/studio-phase1`, `C:/dsv/wt-rebuild` | git worktrees | source | — | not measured (du timed out) | dev | out of scope |

Totals: core `data/storage` 869 MB / 547 files across 17 job directories (two jobs hold 98%: `9b61429f` 727 MB, `8670fb03` 172 MB);
modules 559 MB / 591 files; `analysis/` 142 MB.

Categories the master prompt asked about that **do not exist** as files today: search-response cache (discovery results are stored only
as job JSON artifacts), FFmpeg intermediate frames (the renderer did not leave frame dumps on disk), standalone temp/log/debug
directories under the repo (none found). YouTube acquisition uses `tempfile.TemporaryDirectory()`, which is auto-removed.

## 2. Measured duplication (SHA-256, byte-identical)

68 identical media groups, 180 redundant copies, **536.0 MB redundant** (~36% of the 1.47 GB measured):

| redundant | where the copies live |
|---:|---|
| 341.8 MB | core `render/` + `standalone/video-renderer/storage` (every render exists twice) |
| 74.8 MB | core `assets/` + core `render/` + Visual Module storage |
| 59.9 MB | core `audio/` + Audio Module storage |
| 25.7 MB | within core `render/` (same bytes under two names) |
| 33.7 MB | other combinations |

This is the largest real saving available, and it needs content-addressed storage (one blob, many references), not deletion by age.

## 3. Existing cleanup mechanisms

- `services/voice.py::purge_stale_uploads` (called at API start): removes unreferenced voice uploads after 60 s, **and also removes files that are
  still referenced by `voice_job_requests.temp_path` once they are older than 7 days**. That breaks the new rule "never delete active job files"
  if a voice job sits longer than 7 days. Flagged; not changed in this pass.
- `tempfile.TemporaryDirectory` in YouTube acquisition (self-cleaning).
- `standalone/video-renderer/renderer/main.py` and the audio-module `references.py` contain their own `rmtree`/cleanup paths (module-internal; not audited line by line yet).
- No retention policy, artifact lifecycle state, disk-quota check or cleanup history exists in the core.

## 4. Registry coverage (why a filename/registry-only cleaner would destroy data)

`workflow_artifacts` has 99 rows and `integration_artifacts` 30 rows, against 547 files in core storage. Most files are referenced only from
other tables (`assets`, `asset_assignments`, `voice_job_requests`, …) or from inside JSON artifacts (render manifests, packages, evidence
packages). Any orphan detector must resolve references from **all** of these sources and treat "unresolvable" as `KEEP + REPORT`, never as delete.
Job states (read-only): 16 `AWAITING_REVIEW`, 3 `COMPLETED`, 3 `PAUSED`; no `FAILED`/`CANCELLED` job exists, so failed-job cleanup has nothing to act on today.

## 5. Consequences for the lifecycle manager design

1. New table `storage_artifacts` (schema **v15**, claimed in the control file). It is a registry *overlay*, not a replacement: it points to
   `workflow_artifacts`/`integration_artifacts` rows where they exist, and backfill marks everything found on disk as `UNCLASSIFIED/PRESERVED`
   by default. Nothing becomes deletable until a classifier proves the retention class.
2. The cleanup engine deletes only regular files under an approved root (resolved path, no symlinks or junctions), with a registry row,
   a job not in an active state, an elapsed retention deadline, a sha256/size match at deletion time, and no inbound reference. Dry-run is the default.
3. De-duplication through content addressing is a separate, later change, because it changes how three modules hand over files.
