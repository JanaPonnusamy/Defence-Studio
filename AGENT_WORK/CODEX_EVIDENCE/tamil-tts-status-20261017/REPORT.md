# Tamil TTS and OpenAI prompt/result check — 17 October 2026

**A real OpenAI Tamil speech sample was generated successfully. Defence Studio's production voice catalog still has no Tamil adapter registered.** The new sample uses a standard synthetic voice; it does not create a custom speaker identity.

Live authenticated status shows two ready Windows voices: Hazel (`en-GB`) and Zira (`en-US`). `DEFENCE_ERA_FEMALE_001` remains `NOT_PROVISIONED`; the current owner's voice jobs and profiles are empty. The OpenAI credential is configured in the server environment. No credential or token is included in these artifacts.

Three existing Indic Parler Tamil trial WAVs were verified as present and readable (10.217, 10.612 and 9.764 seconds). They remain isolated trial outputs. The retained Kavitha result required 618.65 seconds of CPU generation for 9.764 seconds of audio. This is historical timing, not a fresh benchmark. No Parler generation or download was repeated.

**New OpenAI speech request and result**

| Field | Actual value |
| --- | --- |
| Endpoint | `POST https://api.openai.com/v1/audio/speech` |
| Model | `gpt-4o-mini-tts` |
| Built-in voice | `marin` |
| Input | 172 characters of neutral Tamil voice-test text, including an AI-generated-voice disclosure |
| Direction | Clear Indian Tamil; calm, confident news presenter; steady medium pace; natural pauses; read exactly |
| Calls | One request; no retries |
| Response | HTTP 200, `audio/wav` |
| Result | 12.5 seconds, 24 kHz mono PCM, 600,044 bytes |
| Elapsed request and validation time | 4.328 seconds |
| Mechanical QC | RMS 0.09496, peak 0.6315, zero clipped samples; WAV parsed successfully |
| SHA-256 | `9343f4d180a376fc426b4aac9056a9cf86f3018815c8ce13991d657c12832bf0` |
| Human listening/pronunciation review | Pending |
| Measured API cost | Unavailable in this response; no exact billing claim made |

The exact submitted body is `tamil-speech-request.json`; the actual sanitized response metadata is `tamil-speech-result.json`; the audio is `tamil-openai-marin-sample.wav`. `generate_sample.py` refuses to rerun when a result exists, preventing an accidental duplicate request.

[Official OpenAI speech documentation](https://developers.openai.com/api/docs/guides/text-to-speech) lists Tamil support and instruction-based delivery control, while noting that built-in voices are optimized for English. Listening is therefore still needed to assess this sample's Tamil pronunciation and suitability. The [speech API reference](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create) documents the request fields used here.

**Existing script-writing prompt/result path**

The script writer uses `backend/defence_studio/services/script_prompt.py`. Its system prompt requires evidence-grounded narration and strict JSON. For Tamil, it requests natural spoken Tamil in Tamil script while preserving technical names and acronyms when needed for accuracy. The user prompt includes audience, tone, target words, approved claim IDs and allowed statement kinds. Its output is structured script JSON, not speech audio.

`backend/defence_studio/providers/openai_text.py` sends this through `/chat/completions`; the frontend's default text model is `gpt-4o-mini`. Sanitized request bodies, raw responses, usage and validation outcomes are supported by `script_inspection.py` and the authenticated script inspection endpoints.

For the current research job `ff16816c-7af1-477a-b5f7-888ebd42f21a`, both English and Tamil inspection lists are empty and there are no script revisions. That job remains at its evidence gate, so there is no OpenAI script response to show for it. The new Tamil speech sample uses neutral test text and was not attached as an approved job script or narration.

**Remaining integration work**

The reusable voice engine and standalone audio module currently register only local/SAPI and test adapters. A production OpenAI TTS adapter still needs credential handling, bounded calls and failure handling, honest delivery-control reporting, catalog/frontend exposure and integration tests. The sample demonstrates API access and valid audio generation; it does not establish that this integration is complete. A Tamil listener should assess the sample before selecting its delivery as the production default.

This task changed only its evidence files and the append-only Codex work log. No application source, existing job, review decision, credential, production voice registration or prior audio was changed. No commit or push was made.
