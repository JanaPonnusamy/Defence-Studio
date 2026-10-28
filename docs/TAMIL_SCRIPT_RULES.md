# GLOBAL TAMIL SCRIPT WRITING AND SENTENCE FORMATION RULES
## Defence Era — Mandatory Default for All Tamil Content

> Adopted 2026-10-28 (user directive). Authoritative for every Tamil output in this project.
> Enforced in code by the Tamil system-prompt instruction in
> `backend/defence_studio/services/script_prompt.py` (`_LANGUAGE_INSTRUCTIONS["ta"]`).
> Number expansion for speech (§5) belongs in TTS text preparation, not in the drafted script:
> the script validator only accepts digit sequences that appear in cited claims.

### 1. Core Requirement

Whenever the requested output language is Tamil, generate natural, complete, grammatically correct, easily pronounceable Tamil suitable for professional spoken narration.

These rules apply to every Tamil-language output, including:
- News scripts and defence news
- Explainers, documentaries and historical narratives
- Introductions, transitions and conclusions
- Headlines, summaries and voice-over scripts
- Tamil subtitles, captions and narration segments
- Mixed Tamil-English technical content

These requirements are mandatory defaults, not optional style suggestions.

### 2. Natural Spoken Tamil

1. Write in natural, standard spoken-narration Tamil with correct grammar and familiar vocabulary.
2. Prefer clear, contemporary Tamil that a professional Tamil newsreader can pronounce naturally.
3. Avoid awkward literal translations from English, machine-translated phrasing, unnatural word order and unnecessarily complicated literary Tamil.
4. Preserve the intended meaning, facts, names, technical terminology and degree of certainty.
5. Do not use colloquial slang, dialect-specific expressions or exaggerated dramatic language unless explicitly requested.
6. Use a consistent Tamil register throughout each script.

### 3. Complete Sentence Formation

1. Every sentence must express a complete, understandable thought.
2. Include the necessary subject, action and supporting information wherever required by Tamil grammar.
3. Use appropriate Tamil case markers, suffixes, tense, agreement, conjunctions and postpositions.
4. Avoid incomplete clauses, dangling phrases, broken word sequences, missing verbs and disconnected fragments.
5. Connect related ideas using natural transitions.
6. Do not combine too many facts into one long sentence.
7. Prefer short-to-medium sentences, generally around 8–20 spoken words when practical. This is a guideline, not a rigid limit.
8. Break long sentences at natural semantic boundaries without changing their meaning.
9. Ensure that pronouns and references clearly identify the people, countries, organisations or events being discussed.
10. Read every paragraph as spoken narration. Rewrite any sentence that sounds unnatural when spoken aloud.

### 4. Tamil Script and Word Integrity

1. Use proper Tamil Unicode text wherever a Tamil word has an established Tamil spelling.
2. Never split a Tamil word into separate fragments, syllables or characters for formatting or chunking.
3. Never insert spaces inside a Tamil word.
4. Do not omit required letters, suffixes, vowel signs or pulli marks.
5. Do not replace Tamil characters with visually similar characters from other scripts.
6. Do not transliterate ordinary Tamil sentences into Latin characters unless specifically requested.
7. Preserve correct spelling and grammatical suffixes during editing, text normalization, sentence splitting and TTS preparation.
8. Keep punctuation outside words and ensure that punctuation does not damage the Unicode sequence.

### 5. Numbers, Dates and Measurements

1. Express numbers in the form most natural for Tamil speech.
2. Expand dates, times, quantities, percentages, prices and measurements into unambiguous spoken forms when required by the TTS engine.
3. Preserve the exact underlying numerical value. Never round, reinterpret or change a number merely to improve pronunciation.
4. Use consistent Tamil number wording throughout a script.
5. Handle abbreviations, decimals, ranges, currency values and measurement units explicitly.
6. Do not assume that a TTS engine will correctly pronounce raw digits, symbols or abbreviations.
7. Maintain the original distinction between confirmed figures, estimates and allegations.

### 6. English Names and Defence Terminology

1. Preserve the official identity of people, countries, military units, aircraft, ships, missiles, weapons, organisations and technical systems.
2. Use established Tamil names when they are accurate and unambiguous.
3. For unfamiliar English names and acronyms, use a consistent, pronunciation-friendly rendering suitable for Tamil narration.
4. Do not translate official product names or military designations in ways that change their identity.
5. Expand an acronym at first mention when doing so helps the audience understand it.
6. Where English technical terminology must remain in the script, ensure the chosen TTS engine can pronounce it correctly.
7. Keep a glossary of recurring names and terminology for consistency across episodes.
8. Never invent a Tamil transliteration and assume it is correct without checking pronunciation.

### 7. Punctuation and Narration Flow

1. Use Tamil punctuation conventions where appropriate and standard punctuation where needed for technical names.
2. Use commas for short pauses, full stops for complete thoughts and paragraph breaks for major changes of subject.
3. Use question marks only for genuine questions.
4. Avoid excessive ellipses, repeated exclamation marks, decorative symbols and punctuation that produces unnatural pauses.
5. Do not place headings, production notes, camera directions or stage instructions in the narration text sent directly to TTS.
6. Keep narration text separate from metadata, subtitles, visual directions and editorial instructions.
7. Use paragraph boundaries to support natural breathing and coherent delivery.

### 8. Factual Accuracy and Editorial Integrity

1. Never invent facts, quotations, statistics, sources, dates, military capabilities or official statements.
2. Distinguish verified facts from estimates, claims, analysis and speculation.
3. Preserve attribution when reporting statements by governments, militaries, analysts or news organisations.
4. Do not make a statement more certain, dramatic or inflammatory than its evidence supports.
5. Improve sentence structure without changing the factual meaning.
6. If a fact is uncertain, phrase it accurately rather than filling the gap with invented information.

### 9. Script Segmentation for TTS

1. Generate and validate complete sentences before splitting a script into TTS segments.
2. Split primarily at paragraph and sentence boundaries.
3. Never cut a Tamil word, number, acronym or named entity across segments.
4. Avoid splitting a grammatical phrase where the resulting audio would sound unnatural.
5. Preserve sentence order and punctuation when concatenating generated audio.
6. Maintain an explicit mapping between each text segment and its generated audio.
7. If a segment fails, regenerate that segment without silently replacing it with a different voice or engine.
8. Verify that every sentence appears in the final audio and that no text has been lost or duplicated.

### 10. Mandatory Validation Before Approval

Before accepting a Tamil script, verify:

- Grammar, spelling and natural sentence formation.
- Complete thoughts and clear references.
- Correct Tamil Unicode and intact words.
- Correct numbers, dates, names and technical terminology.
- Consistent language register and vocabulary.
- Natural spoken rhythm and sensible sentence length.
- Accurate facts and preserved attribution.
- Correct sentence and segment boundaries for TTS.

Reject and rewrite any script containing broken Tamil words, incomplete sentences, unnatural translations, ambiguous number readings or avoidable pronunciation problems.

### 11. Separate Script Quality from Audio Quality

A grammatically correct script does not guarantee correct speech synthesis.

Maintain separate checks for:
- Script correctness
- TTS pronunciation and intelligibility
- Voice naturalness and consistency
- Audio timing and completeness
- Subtitle alignment
- Final rendered-video integrity

Do not mark narration as successful merely because an audio file was created.

### 12. Default Output Policy

Unless the user explicitly requests a different language or style:

- Write Tamil content in natural, complete, professional narration Tamil.
- Prefer clarity and correct pronunciation over unnecessarily elaborate vocabulary.
- Preserve factual accuracy and technical meaning.
- Keep every sentence complete and every Tamil word intact.
- Apply these rules consistently to all Tamil scripts, topics, models and narration providers.

These rules govern script generation and text preparation. They do not replace the independent validation requirements of the TTS engine or the final audio-rendering pipeline.
