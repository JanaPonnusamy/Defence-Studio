"""HTTP routes for cross-module integration stages (RM-3..RM-7).

Kept out of ``app.py`` so each stage adds routes here without growing the
core file. Every route is owner-authenticated and delegates to a service;
errors use the existing ReviewError handler (404/409/422/502).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, File, Form, Header, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field


class VisualPlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: dict = Field(default_factory=dict)
    format: str | None = None


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
    format: str | None = None


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


class YtConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str = Field(min_length=1, max_length=100)


class YtProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str
    data: dict = Field(default_factory=dict)


class YtJobBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    language: str
    package_version: int = Field(gt=0)
    connection_id: str
    profile_id: str | None = None
    overrides: dict = Field(default_factory=dict)


class YtApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_digest: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=1, max_length=500)
    confirmation: str | None = Field(default=None, max_length=100)


class YtEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict = Field(min_length=1)


class YtPartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part: str = Field(pattern="^(thumbnail|captions)$")


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=8, max_length=500)


class MeasuredVisualBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio_version: int = Field(gt=0)
    options: dict = Field(default_factory=dict)
    format: str | None = None


def _with_format(name: str | None, options: dict) -> dict:
    """Format preset first, explicit options win. Unknown preset -> 422."""
    if not name:
        return options
    from defence_studio import formats as fmt
    from defence_studio.services.integration import IntegrationValidationError
    try:
        return {**fmt.visual_options(name), **options}
    except fmt.UnknownFormat as exc:
        raise IntegrationValidationError(str(exc)) from None


def register_integration_routes(app: FastAPI, require_owner, services: dict) -> None:
    visual = services["visual"]
    audio = services["audio"]
    render = services["render"]
    qc = services["qc"]
    package = services["package"]
    yt = services["yt"]

    def idem(value: str) -> str:
        return value

    @app.post("/api/jobs/{job_id}/visual/{language}/plan", status_code=202)
    def start_visual_plan(
        job_id: str, language: str, body: VisualPlanBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return visual.start_visual_plan(job_id, owner_id, language, idempotency_key,
                                        _with_format(body.format, body.options))

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

    @app.get("/api/formats")
    def list_formats(owner_id: str = Depends(require_owner)):
        from defence_studio import formats as fmt
        return {"formats": fmt.listing(), "default": fmt.DEFAULT_FORMAT}

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
        return visual.start_visual_plan(job_id, owner_id, language, idempotency_key,
                                        _with_format(body.format, body.options), audio_version=body.audio_version)

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
        from defence_studio import formats as fmt
        try:
            reqs = {**fmt.qc_requirements(body.format), **body.requirements} if body.format else body.requirements
        except fmt.UnknownFormat as exc:
            from defence_studio.services.integration import IntegrationValidationError
            raise IntegrationValidationError(str(exc)) from None
        return qc.start_qc(job_id, owner_id, language, body.render_version, idempotency_key, requirements=reqs)

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

    # -- RM-8 YouTube ----------------------------------------------------------------

    @app.get("/api/youtube/config")
    def yt_config(owner_id: str = Depends(require_owner)):
        return yt.config_status()

    @app.post("/api/youtube/connect/start")
    def yt_connect_start(owner_id: str = Depends(require_owner)):
        return yt.connect_start(owner_id)

    @app.get("/api/youtube/oauth/callback")
    def yt_callback(state: str = "", code: str | None = None, error: str | None = None):
        # Browser redirect from Google: no bearer token. The single-use state
        # row binds this callback to the owner who started it.
        from urllib.parse import quote
        try:
            result = yt.connect_callback(state, code, error)
        except Exception as exc:  # never show internals / tokens
            return RedirectResponse(f"/#youtube?error={quote(str(exc)[:200])}", status_code=303)
        return RedirectResponse(f"/#youtube?status={quote(result['status'])}", status_code=303)

    @app.get("/api/youtube/connections")
    def yt_connections(owner_id: str = Depends(require_owner)):
        return yt.list_connections(owner_id)

    @app.post("/api/youtube/connections/{connection_id}/confirm")
    def yt_confirm(connection_id: str, body: YtConfirmBody, owner_id: str = Depends(require_owner)):
        return yt.confirm_channel(owner_id, connection_id, body.channel_id)

    @app.post("/api/youtube/connections/{connection_id}/disconnect")
    def yt_disconnect(connection_id: str, owner_id: str = Depends(require_owner)):
        return yt.disconnect(owner_id, connection_id)

    @app.get("/api/youtube/profiles")
    def yt_profiles(owner_id: str = Depends(require_owner)):
        return {"profiles": yt.list_profiles(owner_id)}

    @app.post("/api/youtube/profiles", status_code=201)
    def yt_profile_create(body: YtProfileBody, owner_id: str = Depends(require_owner)):
        return yt.save_profile(owner_id, body.connection_id, body.data)

    @app.put("/api/youtube/profiles/{profile_id}")
    def yt_profile_update(profile_id: str, body: YtProfileBody, owner_id: str = Depends(require_owner)):
        return yt.save_profile(owner_id, body.connection_id, body.data, profile_id)

    @app.post("/api/youtube/jobs", status_code=201)
    def yt_job_create(
        body: YtJobBody,
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        return yt.create_job(owner_id, body.job_id, body.language, body.package_version, body.connection_id,
                             idempotency_key, profile_id=body.profile_id, overrides=body.overrides)

    @app.get("/api/youtube/jobs")
    def yt_jobs(owner_id: str = Depends(require_owner)):
        return yt.list_jobs(owner_id)

    @app.get("/api/youtube/jobs/{publish_id}")
    def yt_job(publish_id: str, owner_id: str = Depends(require_owner)):
        return yt.get_job(owner_id, publish_id)

    @app.patch("/api/youtube/jobs/{publish_id}")
    def yt_job_edit(publish_id: str, body: YtEditBody, owner_id: str = Depends(require_owner)):
        return yt.edit_job(owner_id, publish_id, body.changes)

    @app.post("/api/youtube/jobs/{publish_id}/approve")
    def yt_job_approve(publish_id: str, body: YtApproveBody, owner_id: str = Depends(require_owner)):
        return yt.approve_job(owner_id, publish_id, body.approval_digest, body.note, body.confirmation)

    @app.post("/api/youtube/jobs/{publish_id}/queue", status_code=202)
    def yt_job_queue(publish_id: str, owner_id: str = Depends(require_owner)):
        return yt.queue_job(owner_id, publish_id)

    @app.post("/api/youtube/jobs/{publish_id}/cancel")
    def yt_job_cancel(publish_id: str, owner_id: str = Depends(require_owner)):
        return yt.cancel_job(owner_id, publish_id)

    @app.post("/api/youtube/jobs/{publish_id}/retry-part", status_code=202)
    def yt_job_retry(publish_id: str, body: YtPartBody, owner_id: str = Depends(require_owner)):
        return yt.retry_part(owner_id, publish_id, body.part)

    # -- asset byte ingestion (stills) ----------------------------------------------

    @app.post("/api/jobs/{job_id}/assets/upload", status_code=201)
    async def upload_asset(
        job_id: str,
        file: UploadFile = File(...),
        title: str = Form(...),
        description: str = Form(""),
        creator: str = Form(""),
        source_url: str = Form(""),
        documented_origin: str = Form(""),
        idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
        owner_id: str = Depends(require_owner),
    ):
        data = await file.read(visual.MAX_ASSET_BYTES + 1)
        return visual.ingest_asset_upload(job_id, owner_id, data, {
            "title": title, "description": description, "creator": creator, "source_url": source_url,
            "documented_origin": documented_origin}, idempotency_key)

    # -- RM-9 operations: overview, usage, credentials -------------------------------

    @app.get("/api/jobs/{job_id}/pipeline/{language}")
    def pipeline_overview(job_id: str, language: str, owner_id: str = Depends(require_owner)):
        return yt.pipeline_overview(job_id, owner_id, language)

    @app.get("/api/jobs/{job_id}/usage")
    def usage_report(job_id: str, owner_id: str = Depends(require_owner)):
        return yt.usage_report(job_id, owner_id)

    @app.get("/api/settings/credentials")
    def credential_status(owner_id: str = Depends(require_owner)):
        return yt.credential_status(owner_id)

    @app.put("/api/settings/credentials/{provider}")
    def set_credential(provider: str, body: CredentialBody, owner_id: str = Depends(require_owner)):
        return yt.set_credential(owner_id, provider, body.value)

    @app.delete("/api/settings/credentials/{provider}")
    def delete_credential(provider: str, owner_id: str = Depends(require_owner)):
        return yt.delete_credential(owner_id, provider)
