"""HTTP routes for cross-module integration stages (RM-3..RM-7).

Kept out of ``app.py`` so each stage adds routes here without growing the
core file. Every route is owner-authenticated and delegates to a service;
errors use the existing ReviewError handler (404/409/422/502).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Header
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field


class VisualPlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: dict = Field(default_factory=dict)


class AudioBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(default="local-pyttsx3", min_length=1, max_length=100)
    voice_id: str | None = Field(default=None, max_length=200)
    align: bool = True
    allow_mock: bool = False


class RenderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio_version: int = Field(gt=0)
    visual_version: int = Field(gt=0)
    burn_subtitles: bool = True


class QcBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render_version: int = Field(gt=0)
    requirements: dict = Field(default_factory=dict)


class QcDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qc_version: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=4000)
    acknowledged_warnings: list[str] = Field(default_factory=list)


class PackageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overrides: dict = Field(default_factory=dict)
    music_asset: dict | None = None


class PackageReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    note: str = Field(min_length=1, max_length=4000)


class MeasuredVisualBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio_version: int = Field(gt=0)
    options: dict = Field(default_factory=dict)


def register_integration_routes(app: FastAPI, require_owner, services: dict) -> None:
    visual = services["visual"]
    audio = services["audio"]
    render = services["render"]
    qc = services["qc"]
    package = services["package"]

    def idem(value: str) -> str:
        return value

    @app.post("/api/jobs/{job_id}/visual/{language}/plan", status_code=202)
    def start_visual_plan(
        job_id: str, language: str, body: VisualPlanBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return visual.start_visual_plan(job_id, owner_id, language, idempotency_key, body.options)

    @app.post("/api/jobs/{job_id}/visual/{language}/plan/{version}/retry", status_code=202)
    def retry_visual_plan(
        job_id: str, language: str, version: int,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return visual.retry_visual_plan(job_id, owner_id, language, version, idempotency_key)

    @app.get("/api/jobs/{job_id}/visual/{language}")
    def visual_status(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return visual.visual_status(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/visual/{language}/timeline")
    def visual_timeline(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return visual.export_visual_timeline(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/integration/{language}/{kind}/{version}/events")
    def integration_events(job_id: str, language: str, kind: str, version: int,
                           owner_id: str = Depends(require_owner)):
        return {"events": visual.integration_events(job_id, owner_id, language, kind, version)}

    # -- RM-4 audio -----------------------------------------------------------

    @app.post("/api/jobs/{job_id}/audio/{language}/generate", status_code=202)
    def start_audio(
        job_id: str, language: str, body: AudioBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return audio.start_audio(job_id, owner_id, language, idempotency_key, provider=body.provider,
                                 voice_id=body.voice_id, align=body.align, allow_mock=body.allow_mock)

    @app.post("/api/jobs/{job_id}/audio/{language}/{version}/retry", status_code=202)
    def retry_audio(
        job_id: str, language: str, version: int,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return audio.retry_audio(job_id, owner_id, language, version, idempotency_key)

    @app.get("/api/jobs/{job_id}/audio/{language}")
    def audio_status(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return audio.audio_status(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/audio/{language}/{version}")
    def audio_set(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return audio.read_integration_body(job_id, owner_id, language, "audio_set", version)

    @app.get("/api/jobs/{job_id}/audio/{language}/{version}/verify")
    def audio_verify(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return audio.verify_audio_set(job_id, owner_id, language, version)

    @app.get("/api/jobs/{job_id}/audio/{language}/{version}/scene/{scene_id}/file")
    def audio_scene_file(job_id: str, language: str, version: int, scene_id: str,
                         owner_id: str = Depends(require_owner)):
        body = audio.read_integration_body(job_id, owner_id, language, "audio_set", version)
        entry = next((s for s in body["scenes"] if s["scene_id"] == scene_id), None)
        if entry is None:
            from defence_studio.services.integration import IntegrationNotFound
            raise IntegrationNotFound("Unknown scene in this audio set")
        data = audio.store.read_bytes(job_id, entry["audio_uri"])
        import hashlib
        if hashlib.sha256(data).hexdigest() != entry["audio_sha256"]:
            from defence_studio.services.integration import IntegrationError
            raise IntegrationError("Stored audio failed its integrity check")
        return Response(content=data, media_type="audio/wav", headers={"X-Content-Type-Options": "nosniff"})

    # -- RM-5 render ----------------------------------------------------------

    @app.post("/api/jobs/{job_id}/visual/{language}/plan-measured", status_code=202)
    def start_measured_visual_plan(
        job_id: str, language: str, body: MeasuredVisualBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return visual.start_visual_plan(job_id, owner_id, language, idempotency_key, body.options,
                                        audio_version=body.audio_version)

    @app.post("/api/jobs/{job_id}/render/{language}/start", status_code=202)
    def start_render(
        job_id: str, language: str, body: RenderBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return render.start_render(job_id, owner_id, language, idempotency_key, audio_version=body.audio_version,
                                   visual_version=body.visual_version, burn_subtitles=body.burn_subtitles)

    @app.post("/api/jobs/{job_id}/render/{language}/{version}/retry", status_code=202)
    def retry_render(
        job_id: str, language: str, version: int,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return render.retry_render(job_id, owner_id, language, version, idempotency_key)

    @app.post("/api/jobs/{job_id}/render/{language}/{version}/cancel")
    def cancel_render(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return render.cancel_render(job_id, owner_id, language, version)

    @app.get("/api/jobs/{job_id}/render/{language}")
    def render_status(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return render.render_status(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/render/{language}/{version}")
    def render_detail(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return render.read_integration_body(job_id, owner_id, language, "render", version)

    @app.get("/api/jobs/{job_id}/render/{language}/{version}/file/{which}")
    def render_file(job_id: str, language: str, version: int, which: str, owner_id: str = Depends(require_owner)):
        data, mime = render.render_file(job_id, owner_id, language, version, which)
        return Response(content=data, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})

    # -- RM-6 quality control and final approval ------------------------------

    @app.post("/api/jobs/{job_id}/qc/{language}/run", status_code=202)
    def run_qc(
        job_id: str, language: str, body: QcBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return qc.start_qc(job_id, owner_id, language, body.render_version, idempotency_key,
                           requirements=body.requirements)

    @app.get("/api/jobs/{job_id}/qc/{language}")
    def qc_status(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return qc.qc_status(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/qc/{language}/{version}")
    def qc_report(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return qc.read_report(job_id, owner_id, language, version)

    @app.get("/api/jobs/{job_id}/qc/{language}/{version}/markdown")
    def qc_markdown(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return Response(content=qc.read_report_markdown(job_id, owner_id, language, version),
                        media_type="text/markdown; charset=utf-8")

    @app.post("/api/jobs/{job_id}/qc/{language}/approve", status_code=201)
    def approve_render(
        job_id: str, language: str, body: QcDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return qc.approve_render(job_id, owner_id, language, body.qc_version, body.note,
                                 body.acknowledged_warnings, idempotency_key)

    @app.post("/api/jobs/{job_id}/qc/{language}/reject", status_code=201)
    def reject_render(
        job_id: str, language: str, body: QcDecisionBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return qc.reject_render(job_id, owner_id, language, body.qc_version, body.note, idempotency_key)

    @app.get("/api/jobs/{job_id}/qc/{language}/approved")
    def approved_render(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return qc.approved_render(job_id, owner_id, language)

    # -- RM-7 branding and publishing package ------------------------------------

    @app.post("/api/jobs/{job_id}/package/{language}/build", status_code=202)
    def build_package(
        job_id: str, language: str, body: PackageBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return package.start_package(job_id, owner_id, language, idempotency_key, overrides=body.overrides,
                                     music_asset=body.music_asset)

    @app.get("/api/jobs/{job_id}/package/{language}")
    def package_status(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return package.package_status(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/package/{language}/{version}")
    def package_manifest(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return package.read_integration_body(job_id, owner_id, language, "publish_package", version)

    @app.post("/api/jobs/{job_id}/package/{language}/{version}/review", status_code=201)
    def review_package(job_id: str, language: str, version: int, body: PackageReviewBody,
                       owner_id: str = Depends(require_owner)):
        return package.review_package(job_id, owner_id, language, version, body.decision, body.note)

    @app.get("/api/jobs/{job_id}/package/{language}/{version}/file/{name}")
    def package_file(job_id: str, language: str, version: int, name: str, owner_id: str = Depends(require_owner)):
        data, mime = package.package_file(job_id, owner_id, language, version, name)
        return Response(content=data, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})
