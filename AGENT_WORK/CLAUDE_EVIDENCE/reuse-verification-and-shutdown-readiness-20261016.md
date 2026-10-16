# Defence Studio — Independent Verification of QVault/TTVC Reuse Audit + Cleanup / Git / Shutdown-Readiness

**Date:** 2026-10-16 (Asia/Calcutta) · **Auditor:** Claude (independent verifier; not implementation owner)
**Boundaries honored:** read-only on QVault (`E:\QVault`) and TTVC (`D:\VBDOTNET\TTVC`); no source edit, install, download, render, generation, or paid call; no `.env`/credential value opened; no production data / job `522dc4ce-…` touched; **no git commit/push/history change; no process killed; no app closed; no shutdown.** Only this report file was written (under my own `CLAUDE_EVIDENCE/`, nothing overwritten).

Legend: **PASS** verified good · **FINDING** material, non-blocking · **BLOCKER** must resolve first. Basis: **[V-local]** my own command this pass.

---

## PART A — Independent verification of `CURSOR_EVIDENCE/video-generator-reuse-audit-20261016.md`

### Check 1 — QVault output empty — **PASS**
[V-local] `find -type f` on each: `E:\QVault\output\videos` = **0**, `\shorts` = **0**, `\reels` = **0**, `output\.work\tts_cache` = **0**. Matches the report exactly. **No real QVault quiz MP4 exists** → the report's "no REAL-OUTPUT-VERIFIED QVault video" is correct.

### Check 2 — TTVC `final_output.mp3` exists/duration/format — **PASS**
[V-local] ffprobe (no regeneration): `format_name=mp3`, `duration=132.336000 s`, `size=2648205 bytes`. **Byte-exact and duration-exact** to the report's "mp3, 132.336 s, 2 648 205 bytes." The MP3 is a genuine on-disk artifact.

### Check 3 — TTVC is Python/Tkinter, not VB.NET — **PASS**
[V-local] No `*.vbproj` / `*.sln` / `*.vb` anywhere in the tree. `main.py` present; `app/ui/main_ui.py` line 2–3 `import tkinter as tk` / `from tkinter import scrolledtext, ttk`. The `D:\VBDOTNET\…` path is a **legacy folder name only** — the project is Python 3 + Tkinter (+ edge-tts/pydub/MoviePy per the report). Report correct.

### Check 4 — Edge TTS evidence; what the MP3 proves / does not — **PASS (with the report's own caveat upheld)**
[V-local] TTVC `app/services/audio_service.py`: `import edge_tts`; `VOICE_MAP = {en: en-IN-NeerjaNeural, ta: ta-IN-PallaviNeural}`; `edge_tts.Communicate(text, voice=voice).save(...)` with **no rate/pitch/emotion** args — matches "no prosody." QVault `integrations/tts/edge_provider.py`: `available()` does `import edge_tts`; `ta-IN-PallaviNeural` declared with `base_pitch +12Hz`; real rate/pitch via Edge Communicate; WordBoundary timings.
**What the real MP3 proves:** edge-tts produced a valid, non-empty ~132 s MP3 on this host historically. **What it does NOT prove:** Tamil pronunciation/intelligibility/naturalness, that it is defence-news-suitable, or correct embedded-English handling — **no audio was played/listened to** (report says so; I did not listen either). The report's refusal to claim Tamil quality is correct.

### Check 5 — YouTube reference "HTTP 403, not analyzed" — **FINDING (status claim not reproducible; conclusion still sound)**
[V-local] WebFetch of `https://www.youtube.com/watch?v=kwG9uCDeSk4` returned **HTTP 200** for me this pass (same as my own TTS audit earlier) — **not 403**. The report's "403" was likely a transient/rate-limited response at Cursor's fetch time and is **not independently reproducible**.
**However the report's actual conclusion is correct and unaffected:** WebFetch only ever yields YouTube's SPA page chrome (title "…RJ Toshila", no audio/video), so **no delivery-characteristic analysis is possible by this means regardless of 200 vs 403.** Recommended wording fix for the report/Codex: state "reference not analyzable via WebFetch — page returns only navigation chrome, no audio; delivery-style matching NOT performed," rather than asserting a specific "403." No delivery claim was made, which is the important part.

### Check 6 — Recommendation to add an Edge `TTSProvider` behind Defence Studio's interface — risks (from existing evidence)
Verified the recommendation is **plausible and lower-effort than Parler**, but carries real risks the report should keep front-and-center:
- **Compatibility:** Defence Studio's `VoiceModel`/`TTSAdapter` contract uses numeric `controls` (tempo/energy/pitch_variation/emphasis). Edge exposes rate/pitch (+ voice-level base_pitch), **no emotion/style for Tamil** (Azure/Edge Tamil has no styles — consistent with my prior [V-doc] finding). Mapping `controls` → Edge rate/pitch is partial; `emphasis`/emotion would be unsupported, not faked. **FINDING.**
- **Network dependency:** edge-tts calls Microsoft Edge's online neural endpoint — **not offline**; defence-topic script text would leave the machine. A genuine privacy/availability consideration vs. the offline Parler path. **FINDING.**
- **Tamil voice quality:** `ta-IN-PallaviNeural` is a real Tamil neural voice, but **no native-listener review of Tamil defence narration exists** — advertised capability ≠ accepted quality. **BLOCKER for any Tamil-ready claim.**
- **Licensing/ToS:** edge-tts uses an unofficial path to Microsoft's endpoint; Microsoft's ToS for that usage is **not cleared** here. Needs legal read before production. **FINDING.**
- **Performance:** network-bound (seconds/request), far faster than the CPU-Parler 618 s path, but subject to rate limits/outages. **FINDING (favorable vs. Parler, but not free of risk).**

### Check 7 — Incorrect claims / missing evidence / overlooked reuse
- **Incorrect/soft claim:** the "HTTP 403" (Check 5) — correct it to a general "not analyzable via WebFetch."
- **Appropriately caveated, not errors:** QVault `PROJECT_STATUS.md` is stale re: the video engine (report flags this); TTVC fixed-4 s video has no A/V sync (flagged); QVault jobs don't survive restart (flagged as a prior file-read, not re-proven — reasonable).
- **Overlooked reuse worth noting:** QVault's **real TTS WordBoundary word-timings** (edge_provider) are a genuinely reusable asset for subtitle timing — stronger than Defence Studio's current proportional-chunk fallback, and a better reuse candidate than the render/mix code. The report lists it but could elevate it as the single highest-value reusable component. **FINDING (enhancement, not defect).**
- No evidence was fabricated; every on-disk claim I spot-checked (QVault empty, TTVC MP3, stack identity) is accurate.

**Part A verdict:** the reuse audit is **substantially accurate and honestly caveated**; one factual correction (403→not-reproducible/200) and a few emphasis enhancements. Tamil narration correctly remains **BLOCKED** pending native listening + pronunciation review. The audit is **safe to rely on** for a provider *decision discussion*, not as Tamil acceptance.

---

## PART B — Cleanup / Git / Shutdown-readiness

### Phase 1 — Active work
- [V-local] `git status` churn + Codex log (`CODEX.md` 03:31–03:32) show Codex **completed and VERIFIED** its TTS audit push and reconciled my prior report; Cursor produced its reuse audit (03:xx). No log shows an open `IN_PROGRESS` write right now, **but I cannot positively prove any agent has finished** — "log stopped changing" ≠ "done." Per Phase 1.6, **recorded as a soft blocker: request explicit status confirmation from Codex/Cursor before any deletion.** I did not interrupt or assume completion.
- Orphaned processes from earlier (uvicorn :8041/:8042, trial venv python) may still exist; **none are owned/started by this task** → I will not terminate them (Phase 1 "do not terminate processes you do not own").

### Phase 2 — Temp/cache classification (inventory; nothing deleted)
| Class | Items | Disposition |
|---|---|---|
| **A — safe disposable** | project `__pycache__`/`.pyc` (59 dirs, [V-local], **none git-tracked**); `*.exit`/`*.log` trial markers | Deletable **only** with owner permission; they are Codex's trial evidence → treat as B until confirmed |
| **B — retained evidence (preserve)** | all `AGENT_WORK/*.md` logs; `CLAUDE_EVIDENCE/`, `CURSOR_EVIDENCE/`, `CODEX_EVIDENCE/`; `indic-parler-trial/` reports + 3 WAVs + hashes; trial `.exit`/`.log` | **Preserve.** Codex's log explicitly keeps these as reproducibility evidence for the pushed audit. |
| **C — reusable env/models (no delete w/o separate approval)** | trial `.venv` (~1.4 GB / 45,114 files [V-local]); HF cache `E:\Jana Soft\hf-cache\indic-parler-trial` (~3.77 GB) | **Do not delete.** `.gitignore` already anchors the trial `.venv`. |
| **D — sensitive (never stage/expose)** | TTVC `.env`, any `.env.local`, tokens, `data/defence_studio.sqlite3` | **Not opened, not staged.** |

### Phase 3 — Coordinated cleanup
**No files deleted.** I have no live channel to Codex/Cursor to obtain the required per-owner permission, `AGENT_COORDINATION.md` does not exist, and every disposable candidate is currently serving as another agent's retained evidence for a *pushed* commit. Per Phase 3 ("if permission is unavailable, leave the candidate files untouched and report them"), I left all of them in place. **Recommendation:** the owning agent (Codex) deletes its own `__pycache__`/stale `.exit` markers once it confirms the trial evidence is no longer needed — not the auditor.

### Phase 4 — Git / remote verification — **PASS**
[V-local]:
- All three claimed commits exist locally: `2c22b2e` ("document TTS recovery audit and isolated Parler trial"), `fbd50f8` ("record verified TTS audit push"), `931da89` ("reconcile independent TTS pre-push review").
- **`HEAD == origin/main == 931da89e8e758bfeb1a20793384a3f38f86a332f`** — local and remote **in sync**, no divergence.
- Branch `main`. Working tree: only untracked evidence/reports + `CURSOR.md` (M). **No source changes pending** → nothing to commit (Phase 4.10: no empty commit), and I am not the commit owner regardless. I ran no `git add`, no commit, no push, no fetch mutation.
- Untracked files (my audit, Cursor's reuse audit, trial `.exit` markers, `output/`) are **evidence to retain** (Phase 4.7) — not to be deleted to clean status, and not mine to stage.

### Phase 5 — Shutdown readiness
- **Not confirmed ready.** I cannot verify all agent tasks are complete (Phase 1 soft blocker), and there are orphaned processes (uvicorn :8041/:8042 etc.) I neither own nor can safely identify as idle vs. in-use.
- I started **no** project process this task → there is nothing of mine to stop.
- Per the explicit **SHUTDOWN AUTHORIZATION** rule, I will **not** shut down Windows or close apps automatically; that needs the user's explicit confirmation, and ideally the process owners stopping their own servers first.

---

## Final report (combined)

1. **Agents waited for / completion evidence:** Codex TTS-audit push VERIFIED and reconciled (`CODEX.md` 03:31–03:32; commits on `origin/main`). Cursor reuse audit produced. **Cannot positively prove either is fully idle now** — requesting explicit status confirmation before any deletion (soft blocker).
2. **Permissions obtained:** none (no live inter-agent channel; `AGENT_COORDINATION.md` absent).
3. **Files removed / retained:** **none removed.** All temp/cache candidates retained as another agent's evidence for a pushed commit; sensitive files untouched/unopened.
4. **Git:** branch `main`; commits `2c22b2e`, `fbd50f8`, `931da89` present locally; **HEAD == origin/main == 931da89** (in sync, verified). No commit/push/fetch-mutation by me.
5. **Checks run:** QVault output empty (0/0/0/0) ✓; TTVC MP3 mp3/132.336 s/2 648 205 B ✓; TTVC Python/Tkinter (no VB.NET) ✓; Edge provider `import edge_tts` + Pallavi/Neerja + prosody posture ✓; YouTube ref = HTTP 200 this pass (report's 403 not reproducible) ✓-with-correction.
6. **Remaining blockers / active processes:** native Tamil listening review (BLOCKER for any Tamil-ready claim); orphaned uvicorn :8041/:8042 + trial python processes (ownership unconfirmed — not stopped); agent-completion not positively confirmable.
7. **Shutdown readiness:** **NOT ready to auto-shutdown.** Needs: (a) user's explicit confirmation; (b) process owners to stop their own servers; (c) confirmation no agent is mid-write. Git is already safely pushed and in sync, so no data-loss risk from a *later, user-confirmed* shutdown once processes are cleanly stopped.

**Nothing was deleted, staged, committed, pushed, closed, or shut down. No Tamil production-readiness claim is made.**
