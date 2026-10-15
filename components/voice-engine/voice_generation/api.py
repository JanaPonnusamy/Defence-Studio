from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .direction import DirectionError
from .engine import GenerationError, VoiceGenerationEngine


class GenerateIn(BaseModel):
    script: str
    direction: dict
    require_commercial: bool = False


def create_app(engine: VoiceGenerationEngine) -> FastAPI:
    app = FastAPI(title="Voice Generation")

    @app.post("/voice-generation/generate")
    def generate(b: GenerateIn):
        try:
            return engine.generate(b.script, b.direction, require_commercial=b.require_commercial)
        except DirectionError as e:
            raise HTTPException(422, {"code": e.code, "message": str(e)})
        except GenerationError as e:
            raise HTTPException(422 if e.code != "GENERATION_FAILED" else 502, {"code": e.code, "message": str(e)})

    @app.get("/voice-generation/generations/{gid}")
    def get(gid: str):
        g = engine.describe(gid)
        if not g:
            raise HTTPException(404, "generation not found")
        return g

    @app.get("/voice-generation/generations/{gid}/alignment")
    def alignment(gid: str):
        a = engine.alignment(gid)
        if not a:
            raise HTTPException(404, "alignment not found")
        return a

    return app
