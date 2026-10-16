# Defence Studio — Independent TTS Audit & Pre-Push Review

**Date:** 2026-10-16 (Asia/Calcutta) · **Auditor:** Claude (independent; not implementation owner)
**Scope:** read-only inspection of the Indic Parler-TTS trial, retained TTS evidence, the 618.65 s inference anomaly, the reference video, the delivery profile, and pre-push safety. **No implementation file edited, no model downloaded, no dependency installed, no inference launched, no commit/push, no credential value inspected or printed.** Only this report file was written (under my own `CLAUDE_EVIDENCE/`, not overwriting Codex's `tts-consolidated-recovery-audit-20261016.md`).

Legend: **PASS** = verified good; **FINDING** = material issue, non-blocking; **BLOCKER** = must resolve before the relevant claim/push. Evidence basis: **[V-local]** my own command this pass · **[V-doc]** primary doc · **[V-code]** source read.

---

## 1. Headline

- The Indic Parler-TTS trial produced a **genuine, technically-valid real Tamil WAV** — independently confirmed (ffprobe + byte-exact SHA-256 match). **PASS (mechanical only).**
- **Tamil narration correctly remains BLOCKED** pending native human listening + pronunciation review — no agent has (or may) substitute for that. **PASS (honesty of status).**
- The trial is **NOT integrated** into any `VoiceModel`/provider adapter — it is an isolated script. The production Tamil-provider gate is therefore **not** advanced beyond "a real model can synthesize Tamil on this host." **FINDING.**
- The **618.65 s / 9.764 s** performance ratio is explained and corroborated: **CPU-only inference** (no CUDA). Not a bug; a deployment/hardware reality with real throughput and memory consequences. **FINDING (operational).**
- **Pre-push hygiene:** the untracked trial dir carries a **1.4 GB / 45,114-file `.venv`** — but root `.gitignore` *does* anchor it, so a careful add is safe; a blind `git add -A` is not. A new root **`requirements.txt` pins `parler-tts` from an unauthenticated `git+https` commit** and is not wired into the backend. **FINDING (pre-push).**

---

## 2. Real-audio verification — PASS (mechanical), ACCEPTED: NO

Independently verified `AGENT_WORK/indic-parler-trial/output/tamil_female_youth_trial.wav`:

- **ffprobe [V-local]:** `pcm_s16le, 44100 Hz, mono, duration 9.763991 s, 861228 bytes` — matches `kavitha_trial_report.json` exactly (`duration_s 9.764`, `bytes 861228`).
- **SHA-256 [V-local]:** `c3c355f37583efc427a4497046464e5476453420379a3cb0e25eeeca1b41746d` — **byte-exact match** to the report's recorded hash. The named artifact is the genuine, unmodified retained output.
- Report metadata: `silent: false, clipped: false, nonempty: true, peak 0.438, rms 0.0615` — consistent with real speech envelope.

**This proves only that a real, non-silent, unclipped Tamil-caption WAV exists.** It does **not** prove Tamil pronunciation correctness, intelligibility, youthfulness of the preset, broadcast suitability, or correct handling of the embedded English ("Defence Era") — all of which require a **native Tamil listener**. `tamil_readiness` in the report is correctly `BLOCKED_PENDING_NATIVE_LISTENING_AND_PRONUNCIATION_REVIEW`.

---

## 3. The 618.65 s inference anomaly — FINDING (root cause identified; do not re-measure)

**Root cause = CPU-only inference**, confirmed from the report [V-local]: `device: "cpu"`, `cuda_available: false`, model `ai4bharat/indic-parler-tts` with a `google/flan-t5-large` text encoder. Corroborated across all three retained runs (generation seconds): **Kavitha 618.65 s, Jaya 427.29 s, Jaya-news-desc 770.32 s** — every run CPU, every run ~400–770 s for ~10 s of audio. Model load is *not* the bottleneck (16.97 s for Kavitha; the bottleneck is generation). Peak RSS ~922.6 MB — within a normal workstation envelope.

**Implication (operational, not a defect):** on this host, Indic Parler-TTS yields roughly a **60–75× real-time-slower** synthesis cost and is unusable for interactive or batched-episode production without a GPU. This is a **capacity/deployment decision for the operator**, not something to fix in code. Per the task, I did **not** launch another generation to re-measure.

---

## 4. Integration status — FINDING: trial is NOT wired into the provider contract

`grep -rniE "parler" backend/ standalone/ components/ --include=*.py` (excluding tests) → **no hits** [V-local]. The trial lives entirely under `AGENT_WORK/indic-parler-trial/` as standalone scripts (`generate_tamil_*.py`) using `ParlerTTSForConditionalGeneration` directly. Consistent with Codex's own statement ("no Defence Studio provider adapter"). 

Consequences for the release gates:
- There is **no `VoiceModel` adapter** exposing Parler through the existing `synthesize(text, language, controls)` contract, no provider-registry entry, no `DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES` declaration for it.
- The engine's production adapters still expose only SAPI rate/volume (+ limited post-process pitch) [V-code, prior audits]. **Parler's delivery is driven by a free-text `description` caption**, which the current `controls` contract (`tempo/energy/pitch_variation/emphasis`) does **not** map to. Integrating Parler is therefore a real contract-design task (how `controls` → description), not a drop-in.
- The fail-closed language gate (`check_provider_policy`, `catalog` honesty) is unaffected and still correct.

**Not a defect in the trial** — it's a scoping fact: the trial answers "can this model speak Tamil on this host?" (yes, mechanically), not "is Tamil TTS integrated and accepted?" (no).

---

## 5. Reference video — access limitation (no analysis claimed)

`WebFetch` of `https://www.youtube.com/watch?v=kwG9uCDeSk4` [V-local] returned only page metadata: title *"First kiss is always awkward kiss; When life gives you tangerines EP2 story telling by RJ Toshila"*, channel *"RJ Toshila"*, no description. **No audio was accessible; no delivery characteristics (pace, tone, register, code-mixing) could be observed.** I therefore make **no claim** of having analyzed or listened to the reference around the 167 s timestamp. This is recorded as an access limitation, consistent with prior passes.

---

## 6. Delivery profile — PASS (observable-characteristics framing; no cloning)

The Kavitha `description` [V-local] reads as a specification of **observable delivery characteristics** — "young adult female Tamil news presenter… bright, clear, youthful… medium-fast pace, crisp pronunciation, natural conversational intonation… controlled pitch variation, short natural pauses… not a mature radio announcer, not deep, not slow, not theatrical." It describes *style*, names no real person, and does not reference or attempt to reproduce the identity of the (inaccessible) reference speaker. **This is the correct non-cloning posture.** Whether the produced audio actually *sounds* youthful/clear is unverified (native-listener only).

---

## 7. Provider / Tamil-capability check — PASS (doc + cache), ACCEPTED: NO

- `ai4bharat/indic-parler-tts` supports Tamil [V-doc, prior audit] and is **cached locally** (`E:\Jana Soft\hf-cache\indic-parler-trial`, ~3.77 GB per Codex's audit); the trial ran `local_files_only: True` [V-code] — no live download this pass.
- Controls the implementation *claims* to apply: delivery is requested via the description caption, which Parler honors as a soft style prompt — it is **not** a deterministic pitch/rate/volume API. Any future integration must not claim precise numeric control that Parler does not expose.

---

## 8. Model-loading / safety posture — FINDING (trial-level only; no production reuse path yet)

The trial loads the model per-process (`from_pretrained(...).to('cpu')`), runs one synthesis, exits — so there is **no persistent model reuse, concurrency, or job-state interaction to audit yet** (there is no service wrapper). Peak RSS ~0.92 GB per run [V-local report]. **Before any integration:** persistent model reuse in a long-lived worker would need explicit memory budgeting (model resident ~hundreds of MB–GB), a single-flight/concurrency guard (CPU inference is single-heavy), a bounded timeout (a 600 s+ generation must not block or revive cancelled/terminal jobs), and the existing fail-closed language gate. None of this exists because integration has not started — flagged as the design checklist, not a present defect.

---

## 9. Credential safety — PASS

- No Hugging Face token **value** appears in any trial source file [V-local]: the `hf_[a-z0-9]{20,}` matches found are false positives inside `.venv` library files (numba/sklearn/torch `RECORD`, transformers source), not a secret. The script reads `HF_HOME` from the environment and uses `local_files_only: True`; the report records `hf_token_configured: true` as a boolean, not a value. **No credential is exposed or committed.** I did not inspect or print any token.

---

## 10. Pre-push safety — FINDINGS (must address before `git add`/push)

Current `git status` [V-local]: untracked `AGENT_WORK/indic-parler-trial/`, `requirements.txt`; modified `AGENT_WORK/CODEX.md`; untracked `CODEX_EVIDENCE/tts-consolidated-recovery-audit-20261016.md`. HEAD `11b8677`.

| # | Item | Classification | Detail / required action (Codex owns the fix) |
|---|---|---|---|
| A | Trial `.venv` (1.4 GB, 45,114 files) | **FINDING** (mitigated) | Root `.gitignore` **does** anchor `AGENT_WORK/indic-parler-trial/.venv/` (`git check-ignore` confirms). Safe **only** if Codex adds selectively. A blind `git add -A`/`git add AGENT_WORK` risks pulling `__pycache__` and any non-ignored env debris. **Verify `git status` shows no `.venv/` or `site-packages` staged before commit.** This is the same class of defect as the pre-existing 6,204 tracked env files — do not repeat it. |
| B | `__pycache__` in trial dir | **FINDING** | Confirm it is ignored/not staged before commit. |
| C | Root `requirements.txt` → `parler-tts @ git+https://…@d108732…` | **FINDING** | Pins an **unauthenticated git commit** of a third-party TTS lib. Not wired into `backend/pyproject.toml` (no backend regression [V-local]), but a loose root `requirements.txt` can be mistaken for the canonical install manifest and pulls a large transitive tree. Recommend: move under the trial dir or clearly comment it as *trial-only, not a production dependency*, and record why the git-pin (vs a release) is necessary. |
| D | Large WAVs / 3.77 GB HF cache | **FINDING** | The 3 WAVs (~0.9 MB each) are fine to retain as evidence. The HF cache is outside the repo (`E:\Jana Soft\hf-cache`) — confirm it is **not** added. |
| E | Protected data | **PASS** | `data/defence_studio.sqlite3` not in `git status`; job `522dc4ce-…` has no working-tree change [V-local]. Review history/approvals untouched. |

**Commit/push verdict:** the **evidence and the two real artifacts are sound**; the change set is **safe to commit ONLY after** Codex confirms (A)–(D) — i.e. no venv/cache/pycache staged and `requirements.txt` is correctly scoped. I am not the commit owner and did not stage/commit anything.

---

## 11. Release-gate (T1–T11) delta from this audit

| Gate | Status | This audit's evidence |
|---|---|---|
| T1 real Tamil voice available | **BLOCKED → partially advanced** | A real Tamil model synthesizes on this host (cache + 3 WAVs), but it is **not registered as a provider**; no OS/SAPI Tamil voice exists. |
| T2 genuine Tamil synthesis | **PASS (mechanical) / ACCEPTED: NO** | ffprobe + SHA-256 verified real Tamil WAV; not via the production pipeline, not human-reviewed. |
| T3 language gate | negative **PASS** (unchanged) | fail-closed gate unaffected. |
| T4 integrity + duration | **PASS (this artifact)** | ffprobe 9.764 s, non-silent, unclipped. |
| T5 measured delivery | **NOT VERIFIED** | analyzer not run on the Parler WAV this pass; report gives basic stats only. |
| T6 pronunciation (human) | **BLOCKED** | native listening not done — correctly gated. |
| T7 mixed-language fidelity | **BLOCKED** | embedded "Defence Era" rendering unverified by ear. |
| T8 subtitle/thumbnail rendering | **PASS** (unchanged) | TTC font fix verified prior pass. |
| T9 alignment on real Tamil | **BLOCKED** | no verified aligner; Parler WAVs have no verified text alignment. |
| T10 full Tamil E2E | **BLOCKED** | no integration, no approved episode. |
| T11 licensing clearance | **NOT VERIFIED** | Parler/AI4Bharat MIT [V-doc prior] but full dependency/dataset + the git-pinned `parler-tts` lib license not cleared here. |

---

## 12. Prioritized required fixes (Codex owns all implementation)

1. **(Pre-push, highest)** Confirm no `.venv`/`site-packages`/`__pycache__`/HF-cache is staged; scope `requirements.txt` as trial-only or move it. (§10 A–D.)
2. **(Capacity decision, operator)** CPU inference is ~60–75× slower than real-time with ~0.9 GB RSS; decide GPU vs. accept batch latency before any production Tamil plan. (§3.)
3. **(Integration design)** If Parler is chosen, design the `VoiceModel` adapter + how the `controls` contract maps to Parler's free-text `description`; add persistent-model memory budget, single-flight concurrency guard, bounded timeout, and cancelled/terminal-job fencing. (§4, §8.)
4. **(Blocking acceptance)** Native Tamil-speaker listening + pronunciation/intelligibility review of the retained WAV(s), including the embedded English. Nothing downstream is "accepted" without it. (§2, §6.)
5. **(Hygiene, authorized separately)** The pre-existing 6,204 tracked env files remain an open maintenance item (see prior audit entries).

## 13. Focused tests worth running (NOT a full-suite rerun)

- Run the existing analyzer on `tamil_female_youth_trial.wav` and retain the real `profile` JSON (closes T5 mechanically — still not acceptance).
- If/when an adapter exists: the §8 safety set (timeout, cancellation/late-result fencing, concurrency, language fail-closed) against a mocked Parler + one real local run — **not** repeating the 618 s generation unnecessarily.
- A `git status --porcelain` assertion in CI that no `*/.venv/*` or `site-packages` path is ever staged (prevents repeating the env-tracking defect).

---

**Bottom line:** Real Tamil audio exists and is mechanically sound (independently verified); the 618 s anomaly is CPU-bound, not a bug; status is honestly BLOCKED on native human review and the provider is not yet integrated. The change set is **safe to commit only after the pre-push hygiene items (§10) are confirmed by Codex.** No production-readiness or Tamil-acceptance claim is made. No files edited beyond this report; no commit/push; no credential inspected; protected data untouched.
