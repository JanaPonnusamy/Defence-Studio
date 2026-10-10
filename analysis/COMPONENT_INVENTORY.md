# DefenceStudio — read-only discovery

Date: 2026-10-10 (Asia/Calcutta). Sources: W = `D:\VBDOTNET\whisper_transcription`; Q = `E:\QVault`. Destination root: `E:\Jana Soft\DefenceStudio`.

# Component inventory
33 component groups. **Verified implementation** means inspected source contains relevant logic, not that it passed runtime tests. **Integrated** means traced source call sites. Interfaces/protocols are not feature stubs. Partial/stub/unknown labels are explicit. No application imports or runs occurred.
## W01 — WhisperService

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Verified implementation**. Integration: Integrated: app.py transcription button. Reuse: **Adapt**.

Responsibility: Cached openai-whisper models; word timestamps and optional language.

Limitations: UI does not pass language; first model use may download weights; no concurrency control.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B1**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\whisper_service.py`

Relative to project root: `app/services/whisper_service.py`.

```text
L3: class WhisperService
L8: WhisperService.load_model(cls, model_name: str='base')
L16: WhisperService.transcribe(cls, media_file: str, model_name: str='base', language: str | None=None)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
import whisper
```
## W02 — TranscriptProcessor

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Verified implementation**. Integration: Integrated: app.py SRT and word timeline. Reuse: **Reuse core logic**.

Responsibility: Normalize whitespace, format paragraphs, generate SRT and extract word timeline.

Limitations: Consumes openai-whisper dict, not QVault plain text; timestamp edge cases untested.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B1**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\transcript_processor.py`

Relative to project root: `app/services/transcript_processor.py`.

```text
L4: class TranscriptProcessor
L7: TranscriptProcessor.normalize(text: str) -> str
L13: TranscriptProcessor.format_paragraphs(text: str) -> str
L25: TranscriptProcessor.seconds_to_srt(seconds: float) -> str
L39: TranscriptProcessor.generate_srt(cls, result: dict) -> str
L55: TranscriptProcessor.extract_word_timeline(result: dict)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from datetime import timedelta
import re
```
## W03 — ExportCenter

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Verified implementation**. Integration: Integrated: app.py export button. Reuse: **Adapt**.

Responsibility: UTF-8 TXT/SRT/JSON/timeline output.

Limitations: Overwrites supplied path; no parent creation or project/version partitioning.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B1**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\export_center.py`

Relative to project root: `app/services/export_center.py`.

```text
L4: class ExportCenter
L7: ExportCenter.export_txt(text: str, output_file: str)
L17: ExportCenter.export_srt(srt_content: str, output_file: str)
L27: ExportCenter.export_json(data: dict, output_file: str)
L41: ExportCenter.export_timeline(timeline: list, output_file: str)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from pathlib import Path
import json
```
## W04 — YouTubeService + MetadataController

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Verified implementation**. Integration: Integrated: app.py metadata button. Reuse: **Alternative only**.

Responsibility: yt-dlp metadata mapped to UI fields.

Limitations: Duplicates QVault downloader; no source snapshot or retry policy.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\youtube_service.py`

Relative to project root: `app/services/youtube_service.py`.

```text
L3: class YouTubeService
L6: YouTubeService.get_video_info(url: str) -> dict
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
import yt_dlp
```
### `D:\VBDOTNET\whisper_transcription\app\services\metadata_controller.py`

Relative to project root: `app/services/metadata_controller.py`.

```text
L3: class MetadataController
L6: MetadataController.fetch(url: str)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.services.youtube_service import YouTubeService
```
## W05 — MediaService + SubtitleService

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Verified implementation**. Integration: Integrated: app.py direct calls. Reuse: **Adapt only if needed**.

Responsibility: MP3 extraction and subtitle download.

Limitations: Returns True, not artifact path; English-only subtitles; title-based names.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\media_service.py`

Relative to project root: `app/services/media_service.py`.

```text
L4: class MediaService
L7: MediaService.download_mp3(url: str, output_dir: str)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from pathlib import Path
import yt_dlp
```
### `D:\VBDOTNET\whisper_transcription\app\services\subtitle_service.py`

Relative to project root: `app/services/subtitle_service.py`.

```text
L4: class SubtitleService
L7: SubtitleService.download_subtitles(url: str, output_dir: str)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from pathlib import Path
import yt_dlp
```
## W06 — DownloadController

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Partially implemented**. Integration: Not used by current app.py. Reuse: **Do not copy**.

Responsibility: Thin download dispatch.

Limitations: Calls nonexistent MediaService.download_video; subtitles call omits required output_dir.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **None**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\download_controller.py`

Relative to project root: `app/services/download_controller.py`.

```text
L4: class DownloadController
L7: DownloadController.download_mp3(url: str, output_path: str)
L11: DownloadController.download_video_360(url: str, output_path: str)
L15: DownloadController.download_video_720(url: str, output_path: str)
L19: DownloadController.download_subtitles(url: str)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.services.media_service import MediaService
from app.services.subtitle_service import SubtitleService
```
## W07 — ProjectCache + ProjectManager

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Partially implemented**. Integration: Standalone relative to current entrypoint. Reuse: **Reference only**.

Responsibility: Per-YouTube-ID file paths and SQLite project INSERT.

Limitations: No schema initialization in ProjectManager; no content/version hash; current app does not wire either class.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B4**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\project_cache.py`

Relative to project root: `app/services/project_cache.py`.

```text
L5: class ProjectCache
L10: ProjectCache.project_dir(cls, youtube_id: str)
L16: ProjectCache.audio_file(cls, youtube_id: str)
L20: ProjectCache.transcript_file(cls, youtube_id: str)
L24: ProjectCache.timeline_file(cls, youtube_id: str)
L28: ProjectCache.metadata_file(cls, youtube_id: str)
L32: ProjectCache.save_metadata(cls, youtube_id: str, data: dict)
L39: ProjectCache.transcript_exists(cls, youtube_id: str)
L43: ProjectCache.audio_exists(cls, youtube_id: str)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from pathlib import Path
import json
```
### `D:\VBDOTNET\whisper_transcription\app\services\project_manager.py`

Relative to project root: `app/services/project_manager.py`.

```text
L5: class ProjectManager
L10: ProjectManager.create_project(cls, youtube_id, title)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from pathlib import Path
import sqlite3
```
## W08 — TimelineEngine

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Verified implementation**. Integration: Standalone relative to current entrypoint. Reuse: **Optional reuse**.

Responsibility: Word lookup/search and duration over timestamp list.

Limitations: Linear search; assumes ordered timeline; does not compose video scenes.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B1**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\services\timeline_engine.py`

Relative to project root: `app/services/timeline_engine.py`.

```text
L1: class TimelineEngine
L4: TimelineEngine.find_current_word(timeline: list, current_time: float)
L17: TimelineEngine.search_word(timeline: list, search_text: str)
L28: TimelineEngine.get_duration(timeline: list)
```
## W09 — Streamlit entrypoint + state

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Partially implemented**. Integration: Integrated single-page application. Reuse: **Reference only**.

Responsibility: Buttons, model choice, status and transcript/subtitle views.

Limitations: Newest global MP3 selected instead of request result; editor keys differ from export state; timeline tab has no rendering block.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B6**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app.py`

Relative to project root: `app.py`.

Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.services.export_center import ExportCenter
from app.services.media_service import MediaService
from app.services.metadata_controller import MetadataController
from app.services.session_state_manager import SessionStateManager
from app.services.subtitle_service import SubtitleService
from app.services.transcript_processor import TranscriptProcessor
from app.services.whisper_service import WhisperService
from pathlib import Path
import streamlit as st
```
### `D:\VBDOTNET\whisper_transcription\app\services\session_state_manager.py`

Relative to project root: `app/services/session_state_manager.py`.

```text
L13: class SessionStateManager
L16: SessionStateManager.initialize()
L23: SessionStateManager.reset()
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
import streamlit as st
```
## W10 — Streamlit UI panels

Project: `D:\VBDOTNET\whisper_transcription`. Classification: **Scaffold/stub (first two); verified timeline display**. Integration: Not imported by current app.py. Reuse: **Do not copy**.

Responsibility: Heading-only download panel; fixed-zero metrics; first-250 timeline display.

Limitations: Separate UI files do not establish working callbacks.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **None**, defined in COMPONENT_REUSE_MATRIX.md.
### `D:\VBDOTNET\whisper_transcription\app\ui\download_center.py`

Relative to project root: `app/ui/download_center.py`.

```text
L3: render()
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
import streamlit as st
```
### `D:\VBDOTNET\whisper_transcription\app\ui\dashboard.py`

Relative to project root: `app/ui/dashboard.py`.

```text
L3: render()
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
import streamlit as st
```
### `D:\VBDOTNET\whisper_transcription\app\ui\timeline_panel.py`

Relative to project root: `app/ui/timeline_panel.py`.

```text
L3: render(timeline)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
import streamlit as st
```
## Q01 — YTDLPWrapper / CookieSource

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: research extraction and search. Reuse: **Adapt selected classes**.

Responsibility: Metadata/search, MP3/video/caption downloads; cookie option adapter.

Limitations: English subtitle list; extraction flat flag; wrapper has no returned artifact path; integration file also contains unrelated Instagram code.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\yt_dlp_wrapper.py`

Relative to project root: `backend/app/services/yt_dlp_wrapper.py`.

```text
L24: class YTDLPWrapper
L27: YTDLPWrapper.extract(url: str, extra_opts: dict | None=None)
L39: YTDLPWrapper.download_audio(url, output_template)
L58: YTDLPWrapper.download_video(url, output_template)
L71: YTDLPWrapper.download_subtitles(url, output_template)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.integrations.ytdlp import CookieSource
import yt_dlp
```
### `E:\QVault\backend\app\integrations\ytdlp.py`

Relative to project root: `backend/app/integrations/ytdlp.py`.

```text
L30: class CookieSource
L46: CookieSource.__init__(self, source: str | None=None) -> None
L62: CookieSource.active(self) -> str
L69: CookieSource.describe(self) -> str
L76: CookieSource.apply(self, opts: dict) -> None
L84: log_cookie_source() -> None
L95: class YtDlp
L97: YtDlp.probe_metadata(url: str, source: str | None=None) -> dict
L116: YtDlp.download_video(url: str, output_dir: Path, source: str | None=None) -> dict
L182: class InstagramSession
L202: InstagramSession.ensure_seeded() -> None
L231: InstagramSession.login(username: str, password: str) -> None
L262: InstagramSession.status() -> dict
L288: InstagramSession.logout() -> None
L293: class InstagramQueue
L303: InstagramQueue.list_urls(source_url: str, limit: int) -> list[str]
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.settings import settings
from app.shared.logging import get_logger
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import yt_dlp
```
## Q02 — WhisperWrapper

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: KnowledgeExtractionService. Reuse: **Adapt or replace behind B1 interface**.

Responsibility: faster-whisper CPU int8 base model; lazy singleton.

Limitations: Returns text only, discards timestamps and language info; fixed model.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\whisper_wrapper.py`

Relative to project root: `backend/app/services/whisper_wrapper.py`.

```text
L5: class WhisperWrapper
L10: WhisperWrapper.get_model(cls)
L20: WhisperWrapper.transcribe_file(cls, audio_path)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from faster_whisper import WhisperModel
```
## Q03 — SubtitleParser

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: KnowledgeExtractionService. Reuse: **Adapt**.

Responsibility: VTT to deduplicated plain text.

Limitations: Global line dedup may erase legitimate repetition; destroys timing; not a general SRT parser.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\subtitle_parser.py`

Relative to project root: `backend/app/services/subtitle_parser.py`.

```text
L5: class SubtitleParser
L13: SubtitleParser.to_text(cls, vtt_path)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from pathlib import Path
import re
```
## Q04 — YouTube research source search

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: TOPIC research mode. Reuse: **Adapt**.

Responsibility: ytsearch sources with reference, title, URL, region/language bias.

Limitations: Search results do not establish source authority; only YouTube provider in this service.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\source_search_service.py`

Relative to project root: `backend/app/services/source_search_service.py`.

```text
L6: class SourceSearchService
L15: SourceSearchService.__init__(self, source_type='youtube')
L27: SourceSearchService.search(self, query, limit)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.services.search_providers.youtube_search_provider import YouTubeSearchProvider
```
### `E:\QVault\backend\app\services\search_providers\youtube_search_provider.py`

Relative to project root: `backend/app/services/search_providers/youtube_search_provider.py`.

```text
L7: class YouTubeSearchProvider
L13: YouTubeSearchProvider.search(self, query, limit)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.knowledge_config import KnowledgeConfig
from app.models.search_source import SearchSource
from app.services.search_providers.base_search_provider import BaseSearchProvider
from app.services.yt_dlp_wrapper import YTDLPWrapper
```
### `E:\QVault\backend\app\models\search_source.py`

Relative to project root: `backend/app/models/search_source.py`.

```text
L5: class SearchSource
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from dataclasses import dataclass
```
## Q05 — KnowledgeExtractionService + frame/OCR helpers

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: KnowledgeResearchService. Reuse: **Adapt**.

Responsibility: YouTube transcript + subtitle + sampled-frame OCR, merged text and checksum.

Limitations: Only youtube_video dispatch; default cleanup deletes media/VTT/frames; constructor requires FFmpeg on PATH; OCR Tamil unverified.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\knowledge_extraction_service.py`

Relative to project root: `backend/app/services/knowledge_extraction_service.py`.

```text
L13: class KnowledgeExtractionService
L25: KnowledgeExtractionService.__init__(self)
L33: KnowledgeExtractionService.processing_version(self)
L40: KnowledgeExtractionService.extract(self, document_type, url, source_reference, title, document_dir, progress=None) -> ExtractionResult
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.knowledge_config import PIPELINE_VERSION
from app.models.extraction_result import ExtractionResult
from app.services.knowledge_frame_extraction_service import FrameExtractionService
from app.services.ocr_service import OCRService
from app.services.subtitle_parser import SubtitleParser
from app.services.whisper_wrapper import WhisperWrapper
from app.services.yt_dlp_wrapper import YTDLPWrapper
from pathlib import Path
import hashlib
```
### `E:\QVault\backend\app\services\knowledge_frame_extraction_service.py`

Relative to project root: `backend/app/services/knowledge_frame_extraction_service.py`.

```text
L6: class FrameExtractionService
L15: FrameExtractionService.__init__(self)
L23: FrameExtractionService.extract_frames(self, video_path, output_dir, interval_seconds=None, max_frames=None)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from pathlib import Path
import shutil
import subprocess
```
### `E:\QVault\backend\app\services\ocr_service.py`

Relative to project root: `backend/app/services/ocr_service.py`.

```text
L1: class OCRService
L13: OCRService.get_engine(cls)
L22: OCRService.read_frame(cls, image_path)
L48: OCRService.read_frames(cls, frame_paths)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from rapidocr_onnxruntime import RapidOCR
```
### `E:\QVault\backend\app\models\extraction_result.py`

Relative to project root: `backend/app/models/extraction_result.py`.

```text
L6: class ExtractionResult
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from dataclasses import dataclass
from typing import Optional
```
## Q06 — LLMService + provider adapter

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: research analysis and consensus. Reuse: **Adapt**.

Responsibility: Provider selection, retries, JSON parsing and token/latency metadata.

Limitations: Invalid JSON becomes {}; no schema validation; provider classes share compatible API assumption; price table is historical source data.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B3**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\llm_service.py`

Relative to project root: `backend/app/services/llm_service.py`.

```text
L15: class LLMService
L28: LLMService.__init__(self, provider=None)
L42: LLMService.list_models(self)
L46: LLMService.generate(self, system_prompt, user_prompt, model=None, temperature=0.2, max_tokens=2000, json_mode=False) -> LLMResult
L64: LLMService.generate_json(self, system_prompt, user_prompt, model=None, temperature=0.2, max_tokens=2000)
L112: LLMService.hash_text(text)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.knowledge_config import KnowledgeConfig
from app.models.llm_result import LLMResult
from app.services.llm_providers.openai_provider import AnthropicProvider, GoogleProvider, OllamaProvider, OpenAIProvider, OpenRouterProvider
import hashlib
import json
```
### `E:\QVault\backend\app\services\llm_providers\base_provider.py`

Relative to project root: `backend/app/services/llm_providers/base_provider.py`.

```text
L4: class BaseLLMProvider
L10: BaseLLMProvider.generate(self, system_prompt: str, user_prompt: str, model: str, temperature: float, max_tokens: int, json_mode: bool) -> LLMResult
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.models.llm_result import LLMResult
```
### `E:\QVault\backend\app\services\llm_providers\openai_provider.py`

Relative to project root: `backend/app/services/llm_providers/openai_provider.py`.

```text
L22: class OpenAICompatibleProvider
L34: OpenAICompatibleProvider.__init__(self)
L54: OpenAICompatibleProvider.list_models(self)
L58: OpenAICompatibleProvider.generate(self, system_prompt, user_prompt, model, temperature, max_tokens, json_mode) -> LLMResult
L146: class OpenRouterProvider
L150: class OpenAIProvider
L154: class AnthropicProvider
L158: class GoogleProvider
L162: class OllamaProvider
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.knowledge_config import KnowledgeConfig
from app.models.llm_result import LLMResult
from app.services.llm_providers.base_provider import BaseLLMProvider
from openai import OpenAI
import time
```
### `E:\QVault\backend\app\models\llm_result.py`

Relative to project root: `backend/app/models/llm_result.py`.

```text
L6: class LLMResult
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from dataclasses import dataclass, field
from typing import Any, Dict
```
## Q07 — KnowledgeResearchService + prompts

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: research router. Reuse: **Adapt**.

Responsibility: URL/topic sessions, analysis, evidence-bearing facts, consensus, reports, cancellation.

Limitations: Not independent fact verification; analysis truncates to 24000 chars; no approval gate; in-process executor.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B3**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\knowledge_research_service.py`

Relative to project root: `backend/app/services/knowledge_research_service.py`.

```text
L17: class SessionCancelled
L21: class KnowledgeResearchService
L29: KnowledgeResearchService.__init__(self, executor=None)
L35: KnowledgeResearchService.start_session(self, mode, input_value, source_count, source_type, ai_provider, ai_model, temperature=0.2, max_tokens=4000)
L76: KnowledgeResearchService.get_session(self, session_id)
L79: KnowledgeResearchService.cancel_session(self, session_id)
L97: KnowledgeResearchService.delete_session(self, session_id)
L126: KnowledgeResearchService.list_sessions(self, **filters)
L129: KnowledgeResearchService.get_results(self, session_id)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.knowledge_config import PIPELINE_VERSION, KnowledgeConfig
from app.config.settings import ROOT
from app.models.search_source import SearchSource
from app.repositories.knowledge_research_repository import KnowledgeRepository
from app.services import knowledge_prompts as prompts
from app.services.knowledge_executor import BackgroundTaskExecutor
from app.services.knowledge_extraction_service import KnowledgeExtractionService
from app.services.llm_service import LLMService
from app.services.source_search_service import SourceSearchService
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
```
### `E:\QVault\backend\app\services\knowledge_prompts.py`

Relative to project root: `backend/app/services/knowledge_prompts.py`.

### `E:\QVault\backend\app\services\knowledge_executor.py`

Relative to project root: `backend/app/services/knowledge_executor.py`.

```text
L4: class KnowledgeExecutor
L10: KnowledgeExecutor.submit(self, func, *args, **kwargs)
L14: class BackgroundTaskExecutor
L19: BackgroundTaskExecutor.__init__(self, max_workers=2)
L25: BackgroundTaskExecutor.submit(self, func, *args, **kwargs)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from concurrent.futures import ThreadPoolExecutor
```
## Q08 — Research repository + models

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: research service. Reuse: **Adapt**.

Responsibility: SQLAlchemy session/document/fact/entity/consensus/AI-run persistence.

Limitations: Master document raw-SQLite claim is stale; source uses SessionLocal and dialect-aware models; new project/revision relationships needed.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B3+B4**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\repositories\knowledge_research_repository.py`

Relative to project root: `backend/app/repositories/knowledge_research_repository.py`.

```text
L133: class KnowledgeRepository
L134: KnowledgeRepository.create_session(self, mode, input_value, source_count_requested, source_type, ai_provider, ai_model, storage_directory, pipeline_version, temperature=0.2, max_tokens=4000)
L168: KnowledgeRepository.get_session(self, session_id)
L173: KnowledgeRepository.set_session_storage_directory(self, session_id, storage_directory)
L181: KnowledgeRepository.update_session_progress(self, session_id, status, current_stage, progress)
L191: KnowledgeRepository.set_session_report(self, session_id, report_path, report_markdown_path)
L200: KnowledgeRepository.set_session_error(self, session_id, error_message)
L209: KnowledgeRepository.list_sessions(self, status=None, topic=None, date_from=None, date_to=None, provider=None, source_type=None)
L260: KnowledgeRepository.delete_session(self, session_id)
L270: KnowledgeRepository.create_document(self, session_id, document_type, source_reference, title, url)
L285: KnowledgeRepository.update_document_extraction(self, document_id, language, duration, word_count, character_count, file_size, checksum, processing_version, transcript_path, subtitle_path, ocr_path, merged_text_path)
L317: KnowledgeRepository.update_document_summary(self, document_id, summary_path)
L325: KnowledgeRepository.update_document_status(self, document_id, status, error_message=None)
L334: KnowledgeRepository.get_document(self, document_id)
L339: KnowledgeRepository.list_documents_by_session(self, session_id)
L348: KnowledgeRepository.create_fact(self, session_id, document_id, category, subcategory, value, confidence, evidence, stage, source_document)
L376: KnowledgeRepository.list_facts_by_session(self, session_id)
L385: KnowledgeRepository.create_entity(self, session_id, document_id, entity_name, entity_type, category, confidence, evidence)
L409: KnowledgeRepository.list_entities_by_session(self, session_id)
L418: KnowledgeRepository.create_consensus(self, session_id, common_practices_json, differences_json, conflicting_advice_json, recommendation_json, confidence)
L440: KnowledgeRepository.get_consensus_by_session(self, session_id)
L450: KnowledgeRepository.create_ai_run(self, session_id, document_id, stage, provider, model, prompt_name, prompt_version, system_prompt_hash, user_prompt_hash, temperature, max_tokens, input_tokens, output_tokens, estimated_cost, latency_ms, status)
L492: KnowledgeRepository.list_ai_runs_by_session(self, session_id)
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.database.session import SessionLocal
from app.models.knowledge_research import KnowledgeAiRun, KnowledgeConsensus, KnowledgeDocument, KnowledgeEntity, KnowledgeFact, KnowledgeSession
from sqlalchemy import delete, func, select
```
### `E:\QVault\backend\app\models\knowledge_research.py`

Relative to project root: `backend/app/models/knowledge_research.py`.

```text
L16: class KnowledgeSession
L41: class KnowledgeDocument
L69: class KnowledgeFact
L90: class KnowledgeEntity
L109: class KnowledgeConsensus
L123: class KnowledgeAiRun
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.settings import settings
from app.database.session import Base
from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, ForeignKey, Integer, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column
```
## Q09 — TTS protocol/registry + Edge

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: timeline synthesis and provider API. Reuse: **Adapt**.

Responsibility: SynthesisResult/WordTiming contract, provider registry, streamed audio and word boundaries.

Limitations: Listed ta-IN female voice is mislabeled as accented English; availability only checks import; voice quality and service availability untested; asyncio.run assumes sync worker.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\integrations\tts\__init__.py`

Relative to project root: `backend/app/integrations/tts/__init__.py`.

```text
L23: class WordTiming
L32: class SynthesisResult
L39: class TTSProvider
L45: TTSProvider.available(self) -> bool
L49: TTSProvider.voices(self) -> list[dict]
L53: TTSProvider.synthesize(self, text: str, voice: str | None, out_path: Path, rate: str | None=None, pitch: str | None=None) -> SynthesisResult
L72: register(provider: TTSProvider) -> TTSProvider
L77: get_tts_provider(name: str | None) -> TTSProvider
L91: list_tts_providers() -> list[dict]
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.integrations.tts import azure_provider, edge_provider, elevenlabs_provider, google_provider, kokoro_provider, openai_provider, piper_provider
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable
```
### `E:\QVault\backend\app\integrations\tts\edge_provider.py`

Relative to project root: `backend/app/integrations/tts/edge_provider.py`.

```text
L35: class EdgeProvider
L39: EdgeProvider.available(self) -> bool
L46: EdgeProvider.voices(self) -> list[dict]
L49: EdgeProvider.synthesize(self, text: str, voice: str | None, out_path: Path, rate: str | None=None, pitch: str | None=None) -> SynthesisResult
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.integrations.ffmpeg import FFmpeg
from app.integrations.tts import SynthesisResult, WordTiming, register
from pathlib import Path
import asyncio
import edge_tts
```
## Q10 — AzureProvider

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Registered TTS alternative. Reuse: **Adapt later**.

Responsibility: REST SSML synthesis with Tamil/English female voice entries.

Limitations: No word timings; entire text escaped; no per-term language/phoneme control; configured keys not checked.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\integrations\tts\azure_provider.py`

Relative to project root: `backend/app/integrations/tts/azure_provider.py`.

```text
L21: class AzureProvider
L25: AzureProvider.available(self) -> bool
L28: AzureProvider.voices(self) -> list[dict]
L31: AzureProvider.synthesize(self, text: str, voice: str | None, out_path: Path, rate: str | None=None, pitch: str | None=None) -> SynthesisResult
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.integrations.ffmpeg import FFmpeg
from app.integrations.tts import SynthesisResult, register
from pathlib import Path
from xml.sax.saxutils import escape
import httpx
```
## Q11 — PiperProvider

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Registered TTS alternative. Reuse: **Defer**.

Responsibility: Local binary/model invocation and duration probing.

Limitations: Language/gender unspecified; ignores prosody; WAV can be written to cache .mp3 path; models/binary unverified.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\integrations\tts\piper_provider.py`

Relative to project root: `backend/app/integrations/tts/piper_provider.py`.

```text
L12: class PiperProvider
L16: PiperProvider.available(self) -> bool
L21: PiperProvider.voices(self) -> list[dict]
L26: PiperProvider.synthesize(self, text: str, voice: str | None, out_path: Path, rate: str | None=None, pitch: str | None=None) -> SynthesisResult
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.integrations.ffmpeg import FFmpeg
from app.integrations.tts import SynthesisResult, register
from pathlib import Path
import subprocess
```
## Q12 — VideoScriptService + QuestionItem

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: render pipeline. Reuse: **Adapt substantially**.

Responsibility: Question JSON normalization, English narration segments, pauses and explanation trimming.

Limitations: Quiz-specific roles/options; English hardcoded; source scanning is unsuitable for explicit approved project revisions.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\video_script_service.py`

Relative to project root: `backend/app/services/video_script_service.py`.

```text
L37: class ScriptSegment
L45: trim_solution(solution: str, budget: int) -> str
L66: class VideoScriptService
L69: VideoScriptService.build(self, questions: list[QuestionItem], kind: str, category: str) -> list[ScriptSegment]
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.services.video_source_service import QuestionItem
from dataclasses import dataclass
```
### `E:\QVault\backend\app\services\video_source_service.py`

Relative to project root: `backend/app/services/video_source_service.py`.

```text
L25: class QuestionItem
L34: QuestionItem.answer_text(self) -> str
L39: class SourceFile
L86: class VideoSourceService
L89: VideoSourceService.list_sources(self) -> list[SourceFile]
L121: VideoSourceService.load(self, source_file: str, topic: str | None=None, count: int=25, offset: int=0, shuffle_seed: int | None=None) -> list[QuestionItem]
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.shared.logging import get_logger
from dataclasses import dataclass, field
from pathlib import Path
import json
import random
```
## Q13 — VideoTimelineService / VideoTimeline

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: generation, mixdown, render, SRT. Reuse: **Adapt substantially**.

Responsibility: Measured-audio timeline, SHA1 TTS cache, caption chunks and fallback timing.

Limitations: Quiz-specific scene events; cache omits provider model/version and has no locking; fallback word timings are estimates.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\video_timeline_service.py`

Relative to project root: `backend/app/services/video_timeline_service.py`.

```text
L52: class SubtitleWord
L59: class CaptionChunk
L66: class AudioClip
L72: class SceneTimeline
L87: class VideoTimeline
L99: VideoTimeline.preview(self) -> dict
L141: class VideoTimelineService
L144: VideoTimelineService.__init__(self) -> None
L149: VideoTimelineService.build(self, questions: list[QuestionItem], kind: str, category: str, provider_name: str | None=None, voice: str | None=None, progress=None) -> VideoTimeline
L172: VideoTimelineService.estimate(self, questions: list[QuestionItem], kind: str, category: str) -> VideoTimeline
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.integrations.tts import WordTiming, get_tts_provider
from app.services.video_script_service import EXPLANATION_CHAR_BUDGET, ScriptSegment, VideoScriptService, trim_solution
from app.services.video_source_service import QuestionItem
from app.shared.logging import get_logger
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
```
## Q14 — VideoAudioService

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: generation _render. Reuse: **Adapt**.

Responsibility: FFmpeg decode, clip offsets, optional music, ticks, PCM mixdown and peak scaling.

Limitations: Mono 44.1kHz; full-length RAM buffer; quiz ticks; no loudness target or music ducking.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\video_audio_service.py`

Relative to project root: `backend/app/services/video_audio_service.py`.

```text
L54: class VideoAudioService
L55: VideoAudioService.assemble(self, timeline: VideoTimeline, template: dict, out_path: Path) -> Path
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.services.video_timeline_service import VideoTimeline
from app.shared.logging import get_logger
from pathlib import Path
import numpy as np
import subprocess
import wave
```
## Q15 — VideoRenderService / template helpers

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: generation _render. Reuse: **Adapt substantially**.

Responsibility: Pillow/NumPy frame composition, FFmpeg H264/AAC stream, captions, thumbnail still.

Limitations: Quiz layout, two fixed orientations; no footage/map/image-generation pipeline; Tamil font coverage/shaping unverified.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\video_render_service.py`

Relative to project root: `backend/app/services/video_render_service.py`.

```text
L38: clamp01(x: float) -> float
L42: ease_out_cubic(x: float) -> float
L47: ease_out_back(x: float) -> float
L54: list_templates() -> list[dict]
L71: load_template(key: str) -> dict
L80: class Layout
L113: Layout.content_w(self) -> int
L117: Layout.option_w(self) -> int
L120: Layout.option_pos(self, index: int, count: int) -> tuple[int, int]
L129: Layout.landscape() -> 'Layout'
L145: Layout.portrait() -> 'Layout'
L165: font(template: dict, weight: str, size: int) -> ImageFont.FreeTypeFont
L173: wrap_text(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> list[str]
L187: fit_wrapped(template: dict, weight: str, text: str, box_w: int, box_h: int, size: int, min_size: int=16) -> tuple[ImageFont.FreeTypeFont, list[str], int]
L202: class BackgroundRenderer
L205: BackgroundRenderer.__init__(self, width: int, height: int, template: dict) -> None
L230: BackgroundRenderer.frame(self, t: float) -> tuple[Image.Image, Image.Image, Image.Image | None]
L266: clamp_array(a: np.ndarray) -> np.ndarray
L271: rounded_mask(size: tuple[int, int], radius: int) -> Image.Image
L281: class Card
L291: make_card(template: dict, w: int, h: int, strong: bool=False, border: tuple | None=None) -> Card
L324: make_glow(template: dict, w: int, h: int, color: tuple) -> Image.Image
L333: draw_check_icon(size: int, color: tuple) -> Image.Image
L347: draw_bulb_icon(size: int, color: tuple) -> Image.Image
L358: class SceneSprites
L361: SceneSprites.__init__(self, template: dict, layout: Layout, scene: SceneTimeline) -> None
L488: class VideoRenderService
L491: VideoRenderService.__init__(self, timeline: VideoTimeline, template: dict, orientation: str, fps: int | None=None) -> None
L511: VideoRenderService.render(self, audio_path: Path, out_path: Path, thumbnail_path: Path | None=None, progress=None) -> Path
L556: VideoRenderService.compose_frame(self, t: float) -> Image.Image
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from __future__ import annotations
from app.config.settings import settings
from app.services.video_script_service import OPTION_LETTERS
from app.services.video_timeline_service import SceneTimeline, VideoTimeline
from app.shared.logging import get_logger
from dataclasses import dataclass
from pathlib import Path
import json
import math
import numpy as np
import subprocess
```
## Q16 — write_srt

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: generation _render. Reuse: **Reuse after timeline adaptation**.

Responsibility: UTF-8 SRT from caption chunks with millisecond rounding.

Limitations: Needs VideoTimeline contract; generated timing precision depends on provider/fallback.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\video_subtitle_service.py`

Relative to project root: `backend/app/services/video_subtitle_service.py`.

```text
L18: write_srt(timeline: VideoTimeline, out_path: Path) -> Path
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.services.video_timeline_service import VideoTimeline
from pathlib import Path
```
## Q17 — FFmpeg

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: TTS duration and media helpers. Reuse: **Adapt selected methods**.

Responsibility: Duration/FPS probes and frame extraction.

Limitations: Probe errors may become 0 duration; missing binary not covered by same catch; configured paths differ from research PATH lookup.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B2+B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\integrations\ffmpeg.py`

Relative to project root: `backend/app/integrations/ffmpeg.py`.

```text
L10: class FFmpeg
L12: FFmpeg.probe_duration(video_path: str) -> float
L31: FFmpeg.probe_fps(video_path: str) -> float
L55: FFmpeg.extract_frames(video_path: str, frames_dir: Path, interval: float) -> list[tuple[str, float]]
L76: FFmpeg.extract_frames_sampled(video_path: str, frames_dir: Path, fps: float | None) -> list[tuple[str, float]]
L110: FFmpeg.extract_frames_scene(video_path: str, frames_dir: Path, threshold: float=0.35, sample_fps: float | None=None) -> list[tuple[str, float]]
L149: FFmpeg.extract_single_frame(video_path: str, frames_dir: Path, filename: str, timestamp: float=0.0) -> None
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.settings import settings
from pathlib import Path
import re
import subprocess
```
## Q18 — VideoGenerationService + video history

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: videos router and acquisition worker. Reuse: **Adapt substantially**.

Responsibility: Queue/batch/preview, render semaphore, output registry and error handling.

Limitations: No research/approval/language revision linkage; rereads mutable source on worker; per-process concurrency only.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B4+B5**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\video_generation_service.py`

Relative to project root: `backend/app/services/video_generation_service.py`.

```text
L47: class VideoGenerationService
L48: VideoGenerationService.__init__(self, db: Session)
L55: VideoGenerationService.queue(self, *, source_file: str, kind: str='video', orientation: str | None=None, category: str='General Knowledge', topic: str | None=None, question_count: int | None=None, offset: int=0, shuffle_seed: int | None=None, template: str | None=None, tts_provider: str | None=None, tts_voice: str | None=None, user_id: int | None=None) -> tuple[Video, AcquisitionJob]
L129: VideoGenerationService.queue_batch(self, *, batch_count: int, question_count: int | None=None, **kwargs) -> list[Video]
L151: VideoGenerationService.preview(self, *, source_file: str, kind: str='video', category: str='General Knowledge', topic: str | None=None, question_count: int | None=None, offset: int=0, shuffle_seed: int | None=None) -> dict
L170: VideoGenerationService.run_render(self, job: AcquisitionJob) -> None
L260: VideoGenerationService.delete(self, video: Video) -> None
L267: VideoGenerationService.templates() -> list[dict]
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.core import acquisition_worker
from app.models.acquisition import AcquisitionJob
from app.models.video import Video
from app.repositories.video_repository import VideoRepository
from app.services import notification_service
from app.services.video_audio_service import VideoAudioService
from app.services.video_render_service import VideoRenderService, list_templates, load_template
from app.services.video_source_service import VideoSourceService
from app.services.video_subtitle_service import write_srt
from app.services.video_timeline_service import VideoTimelineService
from app.shared.logging import get_logger
from pathlib import Path
from sqlalchemy.orm import Session
import json
import re
import threading
```
### `E:\QVault\backend\app\models\video.py`

Relative to project root: `backend/app/models/video.py`.

```text
L13: class Video
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.database.session import Base
from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, ForeignKey, Integer, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column
```
### `E:\QVault\backend\app\repositories\video_repository.py`

Relative to project root: `backend/app/repositories/video_repository.py`.

```text
L7: class VideoRepository
L8: VideoRepository.__init__(self, db: Session)
L11: VideoRepository.get(self, video_id: int) -> Video | None
L14: VideoRepository.add(self, video: Video) -> Video
L20: VideoRepository.save(self) -> None
L23: VideoRepository.delete(self, video: Video) -> None
L27: VideoRepository.query(self, search: str | None=None, kind: str | None=None, status: str | None=None, template: str | None=None, limit: int=25, offset: int=0) -> tuple[list[Video], int]
L49: VideoRepository.stats(self) -> dict
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.models.video import Video
from sqlalchemy import func, select
from sqlalchemy.orm import Session
```
## Q19 — Acquisition worker + jobs

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: video_render dispatch confirmed. Reuse: **Adapt selected job code**.

Responsibility: Thread pool, persisted status/progress/error and notifications.

Limitations: Whole files include unrelated NCERT/GK/education; jobs are not a durable broker; users FK; isolate dispatch and models.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B4**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\core\acquisition_worker.py`

Relative to project root: `backend/app/core/acquisition_worker.py`.

```text
L16: submit_job(job_id: int) -> None
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.settings import settings
from app.database.session import SessionLocal
from app.models.acquisition import AcquisitionJob
from app.services import notification_service
from app.services.education_acquisition_service import EducationAcquisitionService
from app.services.gk_scraper_service import GkScraperService
from app.services.knowledge_service import KnowledgeService
from app.services.ncert_service import NcertService
from app.services.video_generation_service import VideoGenerationService
from app.shared.logging import get_logger
from concurrent.futures import ThreadPoolExecutor
```
### `E:\QVault\backend\app\models\acquisition.py`

Relative to project root: `backend/app/models/acquisition.py`.

```text
L24: class AcquisitionJob
L49: class NcertBook
L77: class AcquisitionItem
L116: class GkSite
L133: class Notification
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.config.settings import settings
from app.database.session import Base
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Unicode, UnicodeText, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
```
### `E:\QVault\backend\app\services\notification_service.py`

Relative to project root: `backend/app/services/notification_service.py`.

```text
L7: push(db: Session, level: str, title: str, message: str='', source: str='') -> Notification
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.models.acquisition import Notification
from app.repositories.acquisition_repository import NotificationRepository
from sqlalchemy.orm import Session
```
## Q20 — AcquisitionQueueService + storage DTO

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Standalone from selected research/video flow; other caller wiring not fully traced. Reuse: **Adapt later**.

Responsibility: Idempotent acquisition rows, retry bookkeeping, stuck-item recovery, metadata storage.

Limitations: Not video-job recovery; exam/year paths need project/source mapping; no autonomous scheduler established.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B4**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\services\acquisition_queue_service.py`

Relative to project root: `backend/app/services/acquisition_queue_service.py`.

```text
L27: class AcquisitionQueueService
L28: AcquisitionQueueService.__init__(self, db: Session)
L32: AcquisitionQueueService.enqueue_discovered(self, document: AcquisitionDocument, job_id: int | None=None, commit: bool=True) -> AcquisitionItem
L71: AcquisitionQueueService.mark_downloading(self, item: AcquisitionItem) -> AcquisitionItem
L75: AcquisitionQueueService.mark_downloaded(self, item: AcquisitionItem, local_file: str, checksum: str) -> AcquisitionItem
L82: AcquisitionQueueService.mark_parsed(self, item: AcquisitionItem) -> AcquisitionItem
L86: AcquisitionQueueService.mark_completed(self, item: AcquisitionItem) -> AcquisitionItem
L90: AcquisitionQueueService.mark_failed(self, item: AcquisitionItem, error: str) -> AcquisitionItem
L101: AcquisitionQueueService.recover_stuck(self, older_than_minutes: int=30) -> list[AcquisitionItem]
L107: AcquisitionQueueService.pending(self, provider: str | None=None, limit: int=100) -> list[AcquisitionItem]
L110: AcquisitionQueueService.stats(self) -> dict[str, int]
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.integrations.acquisition.dto import AcquisitionDocument
from app.models.acquisition import ACQUISITION_ITEM_STATUSES, AcquisitionItem
from app.repositories.acquisition_item_repository import AcquisitionItemRepository
from sqlalchemy.orm import Session
import json
```
### `E:\QVault\backend\app\repositories\acquisition_item_repository.py`

Relative to project root: `backend/app/repositories/acquisition_item_repository.py`.

```text
L13: class AcquisitionItemRepository
L14: AcquisitionItemRepository.__init__(self, db: Session)
L17: AcquisitionItemRepository.get(self, item_id: int) -> AcquisitionItem | None
L20: AcquisitionItemRepository.find(self, provider: str, source_id: str) -> AcquisitionItem | None
L26: AcquisitionItemRepository.save(self, item: AcquisitionItem, commit: bool=True) -> AcquisitionItem
L35: AcquisitionItemRepository.delete(self, item: AcquisitionItem) -> None
L39: AcquisitionItemRepository.pending(self, provider: str | None=None, limit: int=100) -> list[AcquisitionItem]
L48: AcquisitionItemRepository.stuck(self, statuses: list[str], older_than_minutes: int) -> list[AcquisitionItem]
L55: AcquisitionItemRepository.list_by_provider(self, provider: str, status: str | None=None, limit: int=50, offset: int=0) -> tuple[list[AcquisitionItem], int]
L71: AcquisitionItemRepository.stats(self) -> dict[str, int]
L77: AcquisitionItemRepository.gk_provider_stats(self) -> dict[str, dict]
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.models.acquisition import AcquisitionItem
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
```
### `E:\QVault\backend\app\integrations\acquisition\dto.py`

Relative to project root: `backend/app/integrations/acquisition/dto.py`.

```text
L20: class AcquisitionDocument
L35: AcquisitionDocument.validate(self) -> list[str]
L51: AcquisitionDocument.is_valid(self) -> bool
L56: class JobSpec
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
```
### `E:\QVault\backend\app\integrations\acquisition\storage.py`

Relative to project root: `backend/app/integrations/acquisition/storage.py`.

```text
L21: class AcquisitionStorage
L23: AcquisitionStorage.path_for(provider: str, exam: str, year: str | int, source_id: str) -> Path
L29: AcquisitionStorage.save(document: AcquisitionDocument, data: bytes, filename: str, exam: str='', year: str | int='') -> Path
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from __future__ import annotations
from app.config.settings import settings
from app.integrations.acquisition.dto import AcquisitionDocument
from dataclasses import asdict
from pathlib import Path
import json
```
## Q21 — Research/video API routers

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: core/app.py includes both. Reuse: **Adapt**.

Responsibility: Permissioned research, generation, history and artifact endpoints.

Limitations: Whole app factory starts DB setup/seed; routers depend on shared schemas/auth/repos; no approval state validation.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B6**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\backend\app\api\routers\research.py`

Relative to project root: `backend/app/api/routers/research.py`.

```text
L33: class SessionCreateRequest
L45: get_providers(_: object=Depends(require_permission(f'{MODULE}:view')))
L73: get_provider_models(provider: str, _: object=Depends(require_permission(f'{MODULE}:view')))
L96: create_session(request: SessionCreateRequest, _: object=Depends(require_permission(f'{MODULE}:execute')))
L118: cancel_session(session_id: int, _: object=Depends(require_permission(f'{MODULE}:execute')))
L137: delete_session(session_id: int, _: object=Depends(require_permission(f'{MODULE}:execute')))
L153: list_sessions(status: str='', topic: str='', date_from: str='', date_to: str='', provider: str='', source_type: str='', _: object=Depends(require_permission(f'{MODULE}:view')))
L175: get_session(session_id: int, _: object=Depends(require_permission(f'{MODULE}:view')))
L188: get_results(session_id: int, _: object=Depends(require_permission(f'{MODULE}:view')))
L201: get_facts(session_id: int, _: object=Depends(require_permission(f'{MODULE}:view')))
L213: get_entities(session_id: int, _: object=Depends(require_permission(f'{MODULE}:view')))
L225: get_consensus(session_id: int, _: object=Depends(require_permission(f'{MODULE}:view')))
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.api.deps import require_permission
from app.config.knowledge_config import PROVIDER_SPECS, KnowledgeConfig
from app.services.knowledge_research_service import KnowledgeResearchService
from app.services.llm_service import LLMService
from app.services.source_search_service import SourceSearchService
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import time
```
### `E:\QVault\backend\app\api\routers\videos.py`

Relative to project root: `backend/app/api/routers/videos.py`.

```text
L52: stats(db: Session=Depends(db_session), _: object=Depends(require_permission(f'{MODULE}:view')))
L60: sources(db: Session=Depends(db_session), _: object=Depends(require_permission(f'{MODULE}:view')))
L77: templates(_: object=Depends(require_permission(f'{MODULE}:view')))
L84: tts_providers(_: object=Depends(require_permission(f'{MODULE}:view')))
L93: generate(payload: VideoGenerateRequest, db: Session=Depends(db_session), user: User=Depends(require_permission(f'{MODULE}:execute')))
L108: generate_batch(payload: VideoBatchRequest, db: Session=Depends(db_session), user: User=Depends(require_permission(f'{MODULE}:execute')))
L127: preview(payload: VideoPreviewRequest, db: Session=Depends(db_session), _: object=Depends(require_permission(f'{MODULE}:view')))
L139: list_videos(search: str | None=None, kind: str | None=None, status: str | None=None, template: str | None=None, limit: int=Query(25, ge=1, le=200), offset: int=Query(0, ge=0), db: Session=Depends(db_session), _: object=Depends(require_permission(f'{MODULE}:view')))
L156: list_jobs(db: Session=Depends(db_session), _: object=Depends(require_permission(f'{MODULE}:view')))
L164: get_video(video_id: int, db: Session=Depends(db_session), _: object=Depends(require_permission(f'{MODULE}:view')))
L176: download(video_id: int, token: str, db: Session=Depends(db_session))
L191: subtitles(video_id: int, token: str, db: Session=Depends(db_session))
L206: thumbnail(video_id: int, token: str, db: Session=Depends(db_session))
L219: delete_video(video_id: int, db: Session=Depends(db_session), _: object=Depends(require_permission(f'{MODULE}:delete')))
```
Direct dependencies (includes imports inside methods; declarations do not prove those branches execute):

```python
from app.api.deps import db_session, require_permission
from app.api.schemas import AcquisitionJobOut, TTSProviderOut, VideoBatchRequest, VideoGenerateRequest, VideoList, VideoOut, VideoPreviewRequest, VideoSourceOut, VideoStats, VideoTemplateOut
from app.integrations.tts import list_tts_providers
from app.models.rbac import User
from app.models.video import Video
from app.repositories.acquisition_repository import AcquisitionJobRepository
from app.repositories.user_repository import UserRepository
from app.repositories.video_repository import VideoRepository
from app.services.video_generation_service import VideoGenerationService
from app.shared.security import decode_access_token
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy.orm import Session
```
## Q22 — React research/video pages

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated: pages call research/video APIs; route registration separately checked. Reuse: **Adapt**.

Responsibility: Research forms/results/history/polling and generation/history/preview controls.

Limitations: React auth/branding/API/types coupling; video preview is estimated timeline; no dual-language approval editor.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B6**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\frontend\src\pages\KnowledgeResearchPage.tsx`

Relative to project root: `frontend/src/pages/KnowledgeResearchPage.tsx`.

```text
L28: export default function KnowledgeResearchPage() {
```
### `E:\QVault\frontend\src\pages\VideoGeneration.tsx`

Relative to project root: `frontend/src/pages/VideoGeneration.tsx`.

```text
L46: export default function VideoGeneration() {
```
## Q23 — JobProgress + Modal

Project: `E:\QVault`. Classification: **Verified implementation**. Integration: Integrated/shared; JobProgress used by video page. Reuse: **Reuse with types/CSS**.

Responsibility: Status/progress rendering and modal structure.

Limitations: Requires Bootstrap CSS and Job type; polling is owned by pages.

Confidence: source inspection; runtime unverified. Dependency/copy boundary: **B6**, defined in COMPONENT_REUSE_MATRIX.md.
### `E:\QVault\frontend\src\components\JobProgress.tsx`

Relative to project root: `frontend/src/components/JobProgress.tsx`.

```text
L16: export function StatusBadge({ status }: { status: string }) {
L20: export default function JobProgress({ job }: { job: Job }) {
```
### `E:\QVault\frontend\src\components\Modal.tsx`

Relative to project root: `frontend/src/components/Modal.tsx`.

```text
L12: export default function Modal({ title, open, onClose, children, footer, size }: ModalProps) {
```
## Configuration evidence (names only; values withheld)

Do not copy live .env, credentials, cookies or database files. Defaults are not proof of configured credentials.
`E:\QVault\backend\app\config\settings.py` exposes these field/accessor names (values redacted):

`_mssql_connect()`, `acquisition_dir()`, `acquisition_job_concurrency`, `admin_email`, `admin_password`, `admin_username`, `api_host`, `api_port`, `app_name`, `assets_dir`, `branding_config_path`, `catalog_dir()`, `cors_origin_list()`, `cors_origins`, `database_url`, `db_backend`, `dedup_mad`, `documents_dir()`, `education_dir()`, `env`, `ffmpeg_path`, `ffprobe_path`, `fonts_dir()`, `frame_max_count`, `frame_min_interval`, `gk_scraper_concurrency`, `gk_scraper_dir()`, `gk_scraper_pool_size`, `gk_scraper_request_delay`, `gk_scraper_timeout`, `instagram_cookies_path()`, `instagram_dir()`, `instagram_session_path()`, `is_sqlite()`, `jobs_dir()`, `jwt_algorithm`, `jwt_expire_minutes`, `jwt_secret`, `log_level`, `merge_threshold`, `mssql_database`, `mssql_driver`, `mssql_encrypt`, `mssql_master_odbc_connect()`, `mssql_odbc_connect()`, `mssql_password`, `mssql_port`, `mssql_server`, `mssql_server_address()`, `mssql_trust_cert`, `mssql_user`, `ncert_concurrent_downloads`, `ncert_dir()`, `ncert_files_base`, `ncert_page_url`, `ncert_retry_count`, `ncert_timeout`, `output_dir`, `question_threshold`, `sqlalchemy_url()`, `storage_dir`, `templates_dir()`, `tts_azure_key`, `tts_azure_region`, `tts_elevenlabs_api_key`, `tts_elevenlabs_model`, `tts_google_api_key`, `tts_openai_api_key`, `tts_openai_model`, `tts_piper_exe`, `tts_piper_model`, `tts_pitch`, `tts_provider`, `tts_rate`, `tts_voice`, `video_concurrent_renders`, `video_fps`, `video_template`, `ytdlp_cookies_file`, `ytdlp_cookies_from_browser`

`E:\QVault\backend\app\config\knowledge_config.py` exposes these field/accessor names (values redacted):

`_load_env_file()`, `default_model()`, `llm_provider()`, `openai_api_key()`, `openai_base_url()`, `provider_api_key()`, `provider_base_url()`, `provider_configured()`, `provider_default_model()`, `provider_spec()`, `search_country()`, `search_lang()`, `storage_root()`

## Selected-source integrity

These hashes identify the exact selected source snapshot used to generate this inventory. They are not a pre-discovery whole-project audit. Files were rehashed after report creation; no differences were found. Only report files and a temporary report-generation script were written.

| Original source | SHA-256 |
|---|---|
| `D:\VBDOTNET\whisper_transcription\app.py` | `b15ef715543495dae66cba09fcf788b417a8f0f6380f215b17497217f6f295cc` |
| `D:\VBDOTNET\whisper_transcription\app\services\download_controller.py` | `fb7b1f953124fa8329d32ce467a02fd64c50bf5686bbd75cd36e37788bba9e29` |
| `D:\VBDOTNET\whisper_transcription\app\services\export_center.py` | `09ea5ae243efa4f5839e00fea608db23984623ed3ec0190972dd06b9eb264ec9` |
| `D:\VBDOTNET\whisper_transcription\app\services\media_service.py` | `f9d347480ca7ab3f74b9c8bd9ef8e1717bf0e379fee7fdf8c49ad9051956c28f` |
| `D:\VBDOTNET\whisper_transcription\app\services\metadata_controller.py` | `87901bab2cec097a35eb6f3c7cc305fa71b4de401bf1d0e1bdd980d2c45cdbad` |
| `D:\VBDOTNET\whisper_transcription\app\services\project_cache.py` | `0c70b659f7ed1dfd8261e051d370f2cf9aa5897a06a4804b5d372683c44a4165` |
| `D:\VBDOTNET\whisper_transcription\app\services\project_manager.py` | `5cccb17db26a99ae8a80e186e655be0e7d0c7f15c2bf5292c1d9c23fd7185938` |
| `D:\VBDOTNET\whisper_transcription\app\services\session_state_manager.py` | `b39f048d75a0e18d54bc803b0e483ad81dae883e0fa97fa991e97829210ac29e` |
| `D:\VBDOTNET\whisper_transcription\app\services\subtitle_service.py` | `6c65b800fc4181d12d233978cff13d8c507a7246d51b8fbb469c1d2571c91ca8` |
| `D:\VBDOTNET\whisper_transcription\app\services\timeline_engine.py` | `5192a90df8dc19d545efdeb582160c63c3f7f20e08e2d6f5d1514a65881f6c0c` |
| `D:\VBDOTNET\whisper_transcription\app\services\transcript_processor.py` | `e1fe2672d3cf7c4dd6191b613a33156bc704570c04933fede8f38d32ffd72496` |
| `D:\VBDOTNET\whisper_transcription\app\services\whisper_service.py` | `7392b5a0e35896cb765278c19a7807164e4d3c4f09a8f5a682fd99f17a77e26d` |
| `D:\VBDOTNET\whisper_transcription\app\services\youtube_service.py` | `4399eeea5c5188a9d3ea380f68b9f3c26faf9daf3a66541fb757a37d67d054f6` |
| `D:\VBDOTNET\whisper_transcription\app\ui\dashboard.py` | `30dd9f85db01e7cb0ce041c9cc7f6fff6e57b509fe0dcbfb70e006c599a8f60e` |
| `D:\VBDOTNET\whisper_transcription\app\ui\download_center.py` | `ca84c959ea069273e0bdbfa436d89c0293ead43c57226cee36566adb0b3358d6` |
| `D:\VBDOTNET\whisper_transcription\app\ui\timeline_panel.py` | `50c5237abb36a044bf27d8bdd2571fc1484c6a082385877781610eca3e63bd57` |
| `E:\QVault\backend\app\api\deps.py` | `e170ea4fe9342355ec895818595584a7b15860dfa6bf597be6684fcdd4ddb9cd` |
| `E:\QVault\backend\app\api\routers\research.py` | `2a755ac1b011221af7e550b7fb73f02ef80060e9523323c8ac74fb11c2505bef` |
| `E:\QVault\backend\app\api\routers\videos.py` | `14ca9175f2dcd5ab4780c72d754216c05b5b7eaab82b5166361608b58102e5b8` |
| `E:\QVault\backend\app\api\schemas.py` | `e8df88c3aefd7dc7d1861d3cc88c741f7ad50febc852773cc30d1206000dbdf1` |
| `E:\QVault\backend\app\config\knowledge_config.py` | `b83510161ef4d76198df9a08b9ff61dc2c46bcd884bab9d901ea50778163a184` |
| `E:\QVault\backend\app\config\settings.py` | `c5a0ab517faddbbae914b2350e53cd4dadefaef8be05cc66f3aea45e36a8ba19` |
| `E:\QVault\backend\app\core\acquisition_worker.py` | `d3e5ef6f97bf028f02fda505c174a1daa34bad5c07af51d497d8284dc6c6ad2c` |
| `E:\QVault\backend\app\core\app.py` | `89f42766e3458381fc6d17090f8eaf90d3f7e469ea999b845a545b40e1d27337` |
| `E:\QVault\backend\app\database\session.py` | `5ca3cd3f531aa31ea71169246f6c6a389f5654cedd68e4bf15ccd011772737c5` |
| `E:\QVault\backend\app\integrations\acquisition\dto.py` | `5d3bcaf8a79c6fa83f9e89d70c4d843f4abe94b5e349482072371491cb339405` |
| `E:\QVault\backend\app\integrations\acquisition\storage.py` | `c2b0f11b6af91a1c114af7667bd3259bec5cd68a3baaf790f57267dc60b7784c` |
| `E:\QVault\backend\app\integrations\ffmpeg.py` | `f1ea761ec2ff87435bbac6d9f5c6842f737088c656b981433b7d71e3d235c6bb` |
| `E:\QVault\backend\app\integrations\tts\__init__.py` | `a53d45916b7cb8129d7440747762deb2b0791085aa9bc628c4253b5874f575da` |
| `E:\QVault\backend\app\integrations\tts\azure_provider.py` | `828f91bab7da523c7e665f41c95337d742c95e80642c9c03de9b96772c82c0d2` |
| `E:\QVault\backend\app\integrations\tts\edge_provider.py` | `eb57641fe8480d92dd4e06cd65ed676c31d90412e437cc0f562ed2e4d0ef40a3` |
| `E:\QVault\backend\app\integrations\tts\piper_provider.py` | `20b3a4cd6179e56ec31313e3f27b9cf5d696071ace46f34ffb97f8ece14ddd5b` |
| `E:\QVault\backend\app\integrations\ytdlp.py` | `c1291c2ac96956f065020a3be2bb9e9cc2ecfd8916f6a0ff16e880b7ee320885` |
| `E:\QVault\backend\app\models\acquisition.py` | `42aceafee823d31536055304d2ec05f4f2810a582713b328c905c62af729ed2f` |
| `E:\QVault\backend\app\models\extraction_result.py` | `9ad2866b83f7a740cda3615a893f1741edc2ac38f42b9cf624ba0792459d04e2` |
| `E:\QVault\backend\app\models\knowledge_research.py` | `5f42cba21a36498e82ed589638f324156fb9437b21aaee42ec726ec157ff5f08` |
| `E:\QVault\backend\app\models\llm_result.py` | `bdce1d531995d298621e33c99569a6b585b89ba6fe392a239f814ec49ce55426` |
| `E:\QVault\backend\app\models\rbac.py` | `00904bd7991a37bdab82b13ace03f710a7aab5b857ab5775bd7ee556f48d08a8` |
| `E:\QVault\backend\app\models\search_source.py` | `b5dad4dd135bf1accb652c3aad775c0a69dbb1bce6cd70283db46dc6edbab6ff` |
| `E:\QVault\backend\app\models\video.py` | `9cdda44ffaf2fd1a94c298fe7179976cdf5fe20df6bd4fc92449ea10ebe2e37d` |
| `E:\QVault\backend\app\repositories\acquisition_item_repository.py` | `ed5e0aa63374a39f00d089b16a70f6b28c37f943645371d4d155158f54add232` |
| `E:\QVault\backend\app\repositories\acquisition_repository.py` | `d65ae65343e29131a5933a1abed9f7a0cabd660419a47c89d6689bed4c8aa4d5` |
| `E:\QVault\backend\app\repositories\knowledge_research_repository.py` | `a40679d3cf590a0e80b64e5930fb655307a7c34157966b12c3d8eb56ad70da3c` |
| `E:\QVault\backend\app\repositories\user_repository.py` | `2df18085240ab49fc71e624447e04a9fbefd004b7f7cba5ec9ea0461515d054a` |
| `E:\QVault\backend\app\repositories\video_repository.py` | `da234a21d7a1e8dab6224c63cb4b7c4b2673991bfd58676e6595b98230f4a08b` |
| `E:\QVault\backend\app\services\acquisition_queue_service.py` | `fbd2296211232f6d6a2a1d99cfb8dcfa4b91f91689332284aebcd74c47695607` |
| `E:\QVault\backend\app\services\knowledge_executor.py` | `d5c659a0192d300fd18ccaf04ef451f84377debc553aac1c12ecac92c3aa3f37` |
| `E:\QVault\backend\app\services\knowledge_extraction_service.py` | `892629036ced942301ab6bf983f56f254d50356ec6d7accc5af3215e55d85f26` |
| `E:\QVault\backend\app\services\knowledge_frame_extraction_service.py` | `8db5d663bae5ca0d778eac19acad59ef6d6dd0a82e0644a73cefbd2796a6a662` |
| `E:\QVault\backend\app\services\knowledge_prompts.py` | `6faed2cad4dbda57c41fa15b6bcadca675fb5b3e6eb7875066c059c793606a54` |
| `E:\QVault\backend\app\services\knowledge_research_service.py` | `46668d77ef26f584a22574c947b578b69796fbcca2a767055b61ff370a2e61ef` |
| `E:\QVault\backend\app\services\llm_providers\base_provider.py` | `01902eee4877984d26b9ea12d80b288a91a7c371fba22cbd1236aadfd573dae2` |
| `E:\QVault\backend\app\services\llm_providers\openai_provider.py` | `1ae1d2a463d9745455df3dc30c87f6b50d925c548d7607598d48ffad506be129` |
| `E:\QVault\backend\app\services\llm_service.py` | `9ab5ff611f4d1c8f6a7cade4e5668c70cdfee937553a53edce9e74af4e7a7c1d` |
| `E:\QVault\backend\app\services\notification_service.py` | `5d02606e94150afb475f450ef355917764c1ba4258a8a622316851304736ae72` |
| `E:\QVault\backend\app\services\ocr_service.py` | `d5c0b589ae19a42e7ea3855f60013f010896b343bef8a344acf4f2e8f5b32862` |
| `E:\QVault\backend\app\services\search_providers\base_search_provider.py` | `79818c250f9bffc620a47649a496a7dac1cb7a9c8d4c0804eaf6b264ec83d98a` |
| `E:\QVault\backend\app\services\search_providers\youtube_search_provider.py` | `fa44712083fa56bcbb52aaf9096d757ebcd22434de1c6733aa4d02c1bc4a2c3f` |
| `E:\QVault\backend\app\services\source_search_service.py` | `ec628312096e43222b5c25a629df6ccd94ed9ec44bb6e95a2b9f7363d00a7afa` |
| `E:\QVault\backend\app\services\subtitle_parser.py` | `edb65c877c69fa119611299dec7db066bde26f3af805d6bc29dc0e21ddb07340` |
| `E:\QVault\backend\app\services\video_audio_service.py` | `8ffcb4756c2f9421ed09f18a578945d28a7cfc2fd6117264e23f049e3e76db23` |
| `E:\QVault\backend\app\services\video_generation_service.py` | `719533f6a4e2fa05d8073252d8e3ddeb94544847dae41260856245909d022050` |
| `E:\QVault\backend\app\services\video_render_service.py` | `56316a3c9a3091ffa8a362e57b8d51a260d02c5ac0d13acdb0d5d9a3acafb1cf` |
| `E:\QVault\backend\app\services\video_script_service.py` | `12b98d68b7483625c71fca655db291169bb8cdcc9f322b4411bc59b55184bf91` |
| `E:\QVault\backend\app\services\video_source_service.py` | `d01abcfed661f6fafee74ae9a170b1ff9c8f438b18c1b91269045172d326f9f4` |
| `E:\QVault\backend\app\services\video_subtitle_service.py` | `0d5ccda93b5bf567f7de046528e6d407851783389dbd717fba36f7375b670563` |
| `E:\QVault\backend\app\services\video_timeline_service.py` | `5ac945aedd0fc5f3ee39610065cab2365ab256190e526b9fb2c2fc823da5b107` |
| `E:\QVault\backend\app\services\whisper_wrapper.py` | `d384852f690e9d961a5041ce7e6a3cc0ccf0bcf4bdcd63a2a6e71916f4b809b4` |
| `E:\QVault\backend\app\services\yt_dlp_wrapper.py` | `02855e49bc128282d0d5ec22acbf7679994b3831ce9f08337342da1b7fdfcb21` |
| `E:\QVault\backend\app\shared\logging.py` | `0559ce0d3e88cae70a5a2eced36b1ec97880786e748460e32490ac3ded4725b5` |
| `E:\QVault\backend\app\shared\security.py` | `d82c79bba11bdb9d3050eab6f88b16e2d5c8aa12279748a0f74f500274becfb6` |
| `E:\QVault\frontend\src\App.tsx` | `787501b99a45e1530051976ce929d8017bf68476f139788bfeaa9f7f330414e7` |
| `E:\QVault\frontend\src\api\client.ts` | `ecb8572671fe95693c590e6b65e3d096f8b41c8ee9fcaa0af81f534e8c14a44d` |
| `E:\QVault\frontend\src\auth\AuthContext.tsx` | `ff975bd47628754ff538b0d3c20b8e352ec7966c8ac1bd0220515932689c06bc` |
| `E:\QVault\frontend\src\branding\BrandingContext.tsx` | `a7d5bf9f9c1fdaac590197968e80032b19454d584ac1a476ae19ee461abcfd43` |
| `E:\QVault\frontend\src\components\JobProgress.tsx` | `d621206e1751f53163fb728be5fa53b9ef8624443eef0b7ea1117436780c11b3` |
| `E:\QVault\frontend\src\components\Modal.tsx` | `fb63db8f18a15af463bee804e90a9fc6da13a6ef9b142c7d4f8000158982e1de` |
| `E:\QVault\frontend\src\modules.ts` | `877fc0a47235c67427199f6d272e468daa1e2bc87a5754882dea906b0aba5bb1` |
| `E:\QVault\frontend\src\pages\KnowledgeResearchPage.tsx` | `26b5e1117ac254102a349242614335c766f990aef8c0cbccf5b5ab0a5e25f612` |
| `E:\QVault\frontend\src\pages\VideoGeneration.tsx` | `9b0d9e5d0113446cfc7df5dd4959d0f286aeab2ebfa112d1078bee30d3792e16` |
| `E:\QVault\frontend\src\services\knowledgeResearchApi.ts` | `73dfc73b290551137e89a31acdbde4372d73239ec7174e049e39245f6f3081db` |
| `E:\QVault\frontend\src\types\knowledgeResearch.ts` | `c68d383eae753c0c5d2d037816b71c0d259aaac9fd3e9c865b0b36fe4a388979` |
