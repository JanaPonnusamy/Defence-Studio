from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .audio import AudioError
from .service import VoiceAnalyzerService


class SourceIn(BaseModel):
    kind: str
    location: str
    authorization_ref: str
    provenance_state: str = "AUTHORIZED"
    metadata: dict = {}
    retain_audio: bool = False


class AnalyzeIn(BaseModel):
    source_id: str
    profile_name: str
    language: str
    transcript: str | None = None
    audio_path: str | None = None
    base_voice_style_id: str | None = None


def create_app(svc: VoiceAnalyzerService | None = None) -> FastAPI:
    svc = svc or VoiceAnalyzerService()
    app = FastAPI(title="Voice Analyzer")

    @app.post("/voice-analyzer/sources")
    def sources(b: SourceIn):
        try:
            return {"source_id": svc.register_source(**b.model_dump())}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/voice-analyzer/analyze")
    def analyze(b: AnalyzeIn):
        try:
            return svc.analyze(**b.model_dump())
        except AudioError as e:
            raise HTTPException(422, {"code": e.code, "message": str(e)})
        except ValueError as e:
            raise HTTPException(404, str(e))

    @app.get("/voice-analyzer/profiles/{vid}")
    def profile(vid: str, version: int | None = None):
        p = svc.profile(vid, version)
        if not p:
            raise HTTPException(404, "profile not found")
        return p

    @app.get("/voice-analyzer/profiles/{vid}/segments")
    def segments(vid: str, version: int | None = None):
        s = svc.segments(vid, version)
        if s is None:
            raise HTTPException(404, "profile not found")
        return {"voice_style_id": vid, "segments": s}

    return app
