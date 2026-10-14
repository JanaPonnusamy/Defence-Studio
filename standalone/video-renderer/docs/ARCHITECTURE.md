# Architecture (Phase R1)

```
 VVM (running, optional)          user
   GET /visual-plans/{id}/timeline     narration.wav
   GET /assets/{id}/file                    |
        \                                   |
         scripts/fetch_from_vvm.py ---------+--> package folder (package.json, timeline.json, narration.wav, assets/)
                                                         |
 POST /renders {package_path | zip}                      v
        |                                      renderer/package.py   validate: schema, paths, sha256, images,
        v                                      renderer/timeline_rules.py   timeline rules, READY->asset
  renderer/main.py (FastAPI)                             |
        |                                                v
  renderer/jobs.py  JobService  --------------->  renderer/render.py
   SQLite (renderer/db.py)   thread pool            layout.py   shot -> frame plan, transitions, duration rules
   QUEUED/RUNNING/...                               motion.py   deterministic crop windows, letterbox
                                                    compose.py  Pillow frames, overlays, GAP card, crossfade
                                                          | rgb24 frames (stdin pipe)
                                                          v
                                                    ffmpeg (user-supplied): libx264 yuv420p + AAC 48 kHz
                                                          |
                                                    media.py    ffprobe + full decode validation
                                                          v
                                      storage/renders/<job_id>/output.mp4 + qc_report.json
```

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | env-driven settings (`RENDERER_*`), storage layout |
| `package.py` | the package contract, path-safe resolution, sha256 + image validation, output-profile resolution |
| `timeline_rules.py` | consumer-side re-implementation of the producer's published timeline rules |
| `wavinfo.py` | stdlib WAV header parser (exact narration duration) |
| `layout.py` | pure functions: boundaries, hold rules, duration check, transition clamping, aggregated warnings |
| `motion.py` | pure functions: motion plan, window at progress p, letterbox rectangle |
| `compose.py` | Pillow composition. Static shots are composed once and cached; motion shots resize a float `box` of the source per frame; overlay boxes are pre-rendered once per shot |
| `render.py` | frame loop -> ffmpeg pipe, cancel polling, progress, validation, atomic publish, QC report |
| `media.py` | ffmpeg/ffprobe discovery + licence flags, encode command, probe/decode validation, peak-memory, orphan-process helpers |
| `jobs.py` / `db.py` | persisted job state machine, cancel, retry, idempotency, restart recovery |
| `main.py` | the minimal API; `upload.py` safe zip extraction |
| `synthetic.py` | locally generated test packages (no downloads) |

## Key decisions

* **Pure planning, impure rendering.** `layout` and `motion` have no I/O, so crop maths, transition clamping and duration rules are unit-tested exactly; only `render.py` touches Pillow/ffmpeg.
* **Frames from real time.** Motion progress is `(frame/fps - shot.start) / shot.duration`, so motion is deterministic and independent of how frames are batched. Shot boundaries use `round(start*fps)`, the producer's own convention.
* **The narration defines the video.** Frame count = `round(audio duration * fps)`; the timeline adapts (hold first/last, cut trailing) within a 5 s tolerance, beyond which the job is rejected. Audio is never trimmed or padded.
* **Never trust, always verify.** A job becomes SUCCEEDED only after the file passes ffprobe + a full decode and has been atomically renamed from `output.partial.mp4`. `output.mp4` therefore never exists for a failed, cancelled or interrupted job.
* **Honest failure.** Missing ffmpeg -> `NOT_AVAILABLE` + `FFMPEG_NOT_AVAILABLE` job failure; encoder death -> `ENCODER_FAILED`; nothing is faked.
* **Cancel kills only its own process.** The loop holds the `Popen` handle of the ffmpeg it started and kills that handle; no name-based process killing.
* **Crash recovery.** On startup `JobService.recover()` fails every RUNNING job with `JOB_INTERRUPTED`, deletes its partial (and any unverified) output, re-queues QUEUED jobs and, if the stored ffmpeg PID is still alive **and its command line names this job's partial file**, terminates that orphan. On Windows a killed parent leaves ffmpeg running until it sees EOF on its stdin pipe; recovery handles both outcomes.
* **Conditional state changes.** Every transition is `UPDATE ... WHERE status IN (legal sources)`, so a cancel and a worker pick-up (or a late completion) cannot both win.
* **Standalone.** Dependencies: FastAPI, SQLAlchemy, Pillow, uvicorn, python-multipart. An AST-based test asserts no import from Defence Studio, VVM, Audio Module, QVault or Whisper Studio. The fetch script talks to the VVM over HTTP GET only.
* **Sibling-stack parity.** Python 3.13, FastAPI, SQLAlchemy + SQLite (WAL), in-process thread-pool queue with restart recovery, pytest, PowerShell 5.1 self-test.

## Performance notes

Cost per frame is dominated by the Pillow crop+resize (BICUBIC on a float box, sources pre-reduced to <= 2x the output) and the pipe write of `W*H*3` bytes. Static shots reuse one composed frame; crossfades compose two. One worker by default (`RENDERER_WORKERS`); x264 `veryfast` uses all cores in ffmpeg while Python composes the next frame. Measured numbers are in `SELF_TEST_REPORT.md`.
