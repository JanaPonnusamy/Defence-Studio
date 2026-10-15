from __future__ import annotations
import hashlib
from .analyzer import analyze_audio, analysis_hash
from .audio import InvalidAudioError
from .store import Store

PROVENANCE = {"AUTHORIZED", "OWNED", "LICENSED"}


class VoiceAnalyzerService:
    def __init__(self, store: Store | None = None, *, max_seconds: float = 3600.0):
        self.store = store or Store()
        self.max_seconds = max_seconds

    def register_source(self, *, kind: str, location: str, authorization_ref: str,
                        provenance_state: str = "AUTHORIZED", metadata: dict | None = None,
                        retain_audio: bool = False) -> str:
        """Stores identifiers/provenance only; raw third-party audio is never copied into the store."""
        if kind not in ("LOCAL_FILE", "URL"):
            raise ValueError("kind must be LOCAL_FILE or URL")
        if provenance_state not in PROVENANCE:
            raise ValueError(f"provenance_state must be one of {sorted(PROVENANCE)}")
        if not authorization_ref or not authorization_ref.strip():
            raise ValueError("authorization_ref is required: analysis only runs on authorized sources")
        return self.store.add_source(kind, location, metadata or {}, provenance_state, authorization_ref, retain_audio)

    def analyze(self, source_id: str, *, profile_name: str, language: str, transcript: str | None = None,
                audio_path: str | None = None, base_voice_style_id: str | None = None,
                note: str | None = None, should_abort=None) -> dict:
        """audio_path: local file (for URL sources, supplied by a separate acquisition layer).
        base_voice_style_id: store the result as a NEW VERSION of that profile (correction path)."""
        src = self.store.get_source(source_id)
        if not src:
            raise ValueError("unknown source_id")
        if base_voice_style_id and not self.store.profile(base_voice_style_id):
            raise ValueError("unknown base_voice_style_id")
        path = audio_path or (src["location"] if src["kind"] == "LOCAL_FILE" else None)
        if not path:
            raise InvalidAudioError("URL source needs an acquired local audio_path (acquisition is a separate layer)")
        res = analyze_audio(path, language=language, transcript=transcript, max_seconds=self.max_seconds)
        if should_abort and should_abort():
            raise InvalidAudioError("analysis aborted (job cancelled); result not published")
        h = analysis_hash(res)
        hit = self.store.find_by_hash(source_id, h)
        if hit and not base_voice_style_id:
            return self.store.profile(*hit)
        vid = base_voice_style_id or "VSP-" + hashlib.sha256((source_id + h).encode()).hexdigest()[:16]
        self.store.save(vid, profile_name, source_id, language, res["profile"], res["segments"], h, note)
        return self.store.profile(vid)

    def profile(self, vid, version=None):
        return self.store.profile(vid, version)

    def segments(self, vid, version=None):
        return self.store.segments(vid, version)
