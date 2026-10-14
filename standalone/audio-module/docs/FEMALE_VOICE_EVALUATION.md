# Female Voice Evaluation

This document covers two separate things, kept clearly apart as instructed:

1. An attempted lightweight check of four reference YouTube videos (titles/existence only — **no
   audio was ever downloaded, extracted, or listened to**).
2. A real, local, objective audit of the actual SAPI5/pyttsx3 voices installed on this machine.

---

## Part A — Reference videos: BLOCKED (by design, not by failure)

This environment has no tool capable of fetching, extracting, or playing back audio from a URL. The
only web-access tool available (`WebFetch`) converts a page to text/markdown and summarizes it with a
small model — it cannot touch audio/video streams at all. I did **not** attempt to download or extract
audio from any of these videos, per the task's explicit instruction and because no authorized
extraction path exists here regardless.

For each URL I made exactly one `WebFetch` call asking only for the page title and channel name (no
audio-related question was asked of the fetch, and none could have been answered):

| # | URL | Title (as returned by the page fetch) | Channel | Page load | Voice/audio quality |
|---|---|---|---|---|---|
| 1 | https://www.youtube.com/watch?v=NfigqozrUEQ | "களமிறங்கும் பாக்கிஸ்தான் I Pakistan entry to Houthi war" | TPdefence (inferred from page `<title>`; not explicitly labeled in the fetched content) | Partially loaded (YouTube's JS-rendered player/description did not come through a plain text fetch; footer/title did) | **Not evaluable with available tools — no audio extraction/listening capability in this environment.** |
| 2 | https://www.youtube.com/watch?v=DF9TguGGbQQ&t=127s | "பாகிஸ்தானில் ரஷ்யாவின் SU57 \| சீனாவின் உதவியுடன் SU57 விமானத்தை காப்பி அடிக்கும் பாகிஸ்தான்" | Not present in the fetched text | Partially loaded | **Not evaluable with available tools — no audio extraction/listening capability in this environment.** |
| 3 | https://www.youtube.com/watch?v=F9EZEuCvdxw | "எங்களிடம் என்ன இருக்கு தெரியுமா? - சவுதியின் அலறல் அறிவிப்பு" | MMK | Loaded, title+channel visible | **Not evaluable with available tools — no audio extraction/listening capability in this environment.** |
| 4 | https://www.youtube.com/watch?v=xN4N-xX1lRw | "விளையாட்டான மரணக் கதை" | Unclear — the fetched text mentions "Toshi Talks" / "RJ Toshila" but does not unambiguously label the uploading channel | Partially loaded | **Not evaluable with available tools — no audio extraction/listening capability in this environment.** |

**No claim is made anywhere in this document about how any speaker in these four videos sounds** —
pitch, tone, gender, clarity, or suitability for narration. All four are Tamil-language channels
(apparently covering defence/geopolitics/news-style content, based only on the titles above), which is
consistent with the likely intent of using one as a reference for Tamil female narration — but the
titles alone say nothing about who is actually speaking in each video or whether the speaker is male or
female, and I have not guessed.

---

## Part B — Actual installed TTS voices: REAL audit, with real objective measurements

### Voice enumeration (two independent, corroborating methods)

**Method 1 — `pyttsx3.init().getProperty('voices')`** (what the module's own `local-pyttsx3` adapter
actually sees and uses):

| id (registry token) | name | languages | gender (engine-reported) | age |
|---|---|---|---|---|
| `TTS_MS_EN-GB_HAZEL_11.0` | Microsoft Hazel Desktop - English (Great Britain) | en-GB | **Female** | Adult |
| `TTS_MS_EN-US_ZIRA_11.0` | Microsoft Zira Desktop - English (United States) | en-US | **Female** | Adult |

**Method 2 — raw registry read**, `HKLM:\SOFTWARE\Microsoft\Speech\Voices\Tokens\*\Attributes` (and
the mirrored `WOW6432Node` path, identical results):

```
Token: TTS_MS_EN-GB_HAZEL_11.0   Gender: Female  Age: Adult  Language: 809 (en-GB)  Name: Microsoft Hazel Desktop
Token: TTS_MS_EN-US_ZIRA_11.0    Gender: Female  Age: Adult  Language: 409 (en-US)  Name: Microsoft Zira Desktop
```

Both methods agree. **Gender is explicit engine/registry metadata, not inferred from the name** — the
task specifically asked that this not be guessed, and it was not.

**Only 2 voices exist on this machine, both female, both English.** There is no male voice and no
Tamil (or any other non-English) voice installed. This is a genuine hardware/OS-install limitation, not
a module bug.

### Synthesis tests (via the module's own real `local-pyttsx3` adapter code path)

Three passages were synthesized through each of the two female voices, using
`app/tts/local_pyttsx3.py` directly (the same code the API uses):

1. **Short English news paragraph** (19 words)
2. **Tamil paragraph** — attempted against both English-only voices specifically to test what happens,
   since the task asked to state plainly if unsupported rather than fake it
3. **Longer passage** (62 words, multiple clauses, a list, a question) to check consistency

| Voice | Passage | Requested lang | Duration (s) | Sample rate | Peak | Clipping | RMS (dBFS proxy) | Voiced-frame ratio | F0 mean (Hz) | Duration-vs-wordcount plausible | `validate_generated_audio` status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Zira (en-US) | news_short | en | 7.338 | 22050 | 0.4035 | No | -23.03 | 0.691 | 174.5 | Yes | PASSED |
| Zira (en-US) | **tamil_attempt** | ta | **0.000** | 22050 | 0.0 | No | -120.0 | 0.0 | — | No (0s for 2 words) | **FAILED** (empty_audio) |
| Zira (en-US) | longer_passage | en | 25.11 | 22050 | 0.4658 | No | -24.19 | 0.529 | 179.3 | Yes | PASSED |
| Hazel (en-GB) | news_short | en | 8.857 | 22050 | 0.5186 | No | -20.46 | 0.586 | 200.6 | Yes | PASSED |
| Hazel (en-GB) | **tamil_attempt** | ta | **0.000** | 22050 | 0.0 | No | -120.0 | 0.0 | — | No (0s for 2 words) | **FAILED** (empty_audio) |
| Hazel (en-GB) | longer_passage | en | 28.396 | 22050 | 0.5545 | No | -21.82 | 0.375 | 191.1 | Yes | PASSED |

**Tamil result, stated plainly: Tamil synthesis is NOT supported by either installed voice.** The
engine does not fabricate garbled/incorrect English-phoneme output when given Tamil script — it
produces literally zero audio samples (0.0s duration), which `validate_generated_audio` correctly
flags as `FAILED` (`empty_audio`). This was double-checked by reading the requested Tamil text back out
of the database by Unicode codepoint (not by eye, since my terminal could not render it correctly) to
confirm the *input* text was transmitted and stored correctly and the failure is genuinely on the
synthesis side, not a request-encoding bug. See `ADMIN_SELF_TEST_REPORT.md` section 6 for that
verification.

### Honesty about "intelligibility"

**I have no way to literally listen to any of this audio.** Every statement below is an objective
signal-processing measurement, not a listening judgment, and is labeled as such:

- **No clipping** on any of the 4 successful (English) syntheses (`peak < 0.56` in all cases, well
  under the `1.0` clip ceiling; `validate_generated_audio`'s own clipping check agrees).
- **Voiced-frame ratio** (speech-active time ÷ total duration, from the same VAD code used by the main
  pipeline) ranged 0.375–0.691 — consistent with normal speech-with-pauses, not silence and not a
  constant tone.
- **F0 mean** (174–201 Hz) is in a plausible adult-female pitch range using the module's own
  `librosa.pyin`-based pitch tracker (this is a corroborating *acoustic* signal consistent with the
  explicit Female engine metadata — it is not independent proof by itself, since this is a formant/F0
  heuristic, not a gender classifier).
- **Duration-vs-word-count plausibility**: for the two English passages per voice, the measured
  duration falls inside a generous `[words/4 wpm-ceiling, words/0.8 wpm-floor]` sanity band for both
  voices, meaning the engine produced a duration consistent with actually having spoken the given text,
  not a truncated or runaway output.
- **No decode/output integrity failures**: all 4 English output files decoded cleanly with
  `librosa`/`soundfile`; both Tamil attempts honestly reported 0-length rather than corrupt or
  misleading data.

None of the above is a substitute for actually listening to the audio, and no claim is made that it is.

### Recommendation

- **No acceptable Tamil female voice exists on this machine today.** Both installed SAPI5 voices are
  English-only.
- Realistic options if Tamil (or any non-English) female narration is required, **named for the
  user's own evaluation — nothing here was installed or silently switched to**:
  - **Open-source / self-hostable**: Coqui TTS / `XTTS-v2` (multilingual, supports voice cloning from a
    short reference clip — would need the user's own authorized reference audio, GPU strongly
    recommended), Piper TTS (lightweight, has some Indic-language community voices, CPU-friendly,
    license varies per voice model), Mozilla/Common Voice-derived models.
  - **Cloud (paid, requires the user's own account/credentials — none were added or assumed here)**:
    Microsoft Azure Neural TTS (has Tamil `ta-IN` female neural voices), Google Cloud TTS (Tamil
    `ta-IN` voices), Amazon Polly (more limited Tamil support — check current region/voice list),
    ElevenLabs (multilingual, strong quality, is a paid API).
  - Any of the above would be added as an **additional adapter** behind the existing
    `app/tts/base.py` provider-neutral interface, exactly the same way `local-pyttsx3` and `mock-tts`
    were — no core pipeline code would need to change. This was not implemented in this pass since it
    would require either installing an untrusted large model or a paid cloud credential, both
    explicitly out of scope here.

### Mock-TTS separation (reiterated for this section specifically)

Everything in this document used the **real** `local-pyttsx3` adapter. `mock-tts` was not used anywhere
in this voice audit, and nothing here should be read as describing the mock adapter's output (which is
a deliberately artificial placeholder tone, documented separately in the main README and the admin
self-test report).
