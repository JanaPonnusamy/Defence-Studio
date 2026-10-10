# DefenceStudio — read-only discovery

Date: 2026-10-10 (Asia/Calcutta). Sources: W = `D:\VBDOTNET\whisper_transcription`; Q = `E:\QVault`. Destination root: `E:\Jana Soft\DefenceStudio`.


# Recommended build order — future work only

First implementation slice: one project with one approved research revision, two reviewed scripts (Tamil with protected English technical terms; separate English), and two short narrated previews. Publishing comes later. No implementation began.

1. **Isolate configuration and define project/history.** B0 and selected B4 persistence. Define project_id, research_revision_id, source hashes, claim/scene IDs, approval actor/time and language versions. All paths point to DefenceStudio. Do not use YouTube ID as the production project's primary identity.
2. **Research and evidence.** B1 transcription utilities, B2 intake, B3 research/LLM. Preserve URL/title/retrieval metadata, excerpts/time ranges, hashes and extraction version. Start with explicit reviewed sources; broaden source providers later.
3. **Fact/script approval.** Schema-validated claims and draft scripts; human review. Freeze approved revisions and invalidate approval after changes. LLM confidence does not grant approval.
4. **Paired language scripts.** Generate/review Tamil and English from the same approved claims and shared scene IDs. Keep glossary entries for canonical English terms, display/spoken form, acronyms and numbers. Review semantic parity. Synthesis receives approved text, not a request to translate.
5. **Female narration.** Adapt Edge contract/provider first, evaluating declared Tamil/English female voices with short samples. Preserve word events; mark fallback timing as estimated. Build separate measured timelines. Azure is optional after quality/alignment evaluation.
6. **Narrative scenes.** Replace quiz QuestionItem/options/countdown/reveal across script/timeline/renderer together. Reuse frame/template/thumbnail primitives. Add source-credited footage/maps/illustrations as new capabilities; verify Tamil fonts.
7. **Audio and FFmpeg rendering.** Adapt B5 mixdown/streaming; render each language separately with shared scene intent and independent timing. Add version-aware atomic cache and explicit output folders.
8. **Captions/export.** Q write_srt for generated narration; W processor/export for source transcripts and needed exports. Separate MP4/SRT/text/thumbnail/provenance per language.
9. **Preview and final approval.** B6 UI/API sections; distinguish estimated timeline preview from actual video playback. Approvals reference exact language revision and output hash.
10. **YouTube scheduling.** New account/channel authorization, upload/idempotency/retry, language metadata, timezone/schedule and persisted video ID/status, after final approval.
11. **Analytics/publishing history.** New synchronization joining channel/video IDs to project/language/render revision.

## Proposed artifact layout (not created)

`projects/<project_id>/research/<revision>/`: shared sources/claims/approval.
`projects/<project_id>/scripts/<revision>/ta-IN/` and `en-IN/`: reviewed scripts.
`projects/<project_id>/renders/<revision>/ta-IN/` and `en-IN/`: separate audio, timeline, MP4, SRT, thumbnail.
`projects/<project_id>/manifest.json`: common approved research/scene plan and both output records. Database version records handle lifecycle/concurrency; manifests provide portable exports.

## Exact first reuse inputs

COMPONENT_REUSE_MATRIX.md defines B0-B6 file lists and dependent interfaces. Begin with new isolation/project/approval contracts and small B1/B3/TTS extractions, then adapt the coupled timeline/render set. These are adaptation inputs, not an unchanged runnable bundle. Do not copy either whole app or runtime data tree.
