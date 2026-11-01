"""Voice Lab HTTP API (``/api/voice-lab/*``), owner-authenticated. Contract: docs/voice_lab/VOICE_LAB_API.md."""
from __future__ import annotations

import os
from contextlib import closing
from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from defence_studio.voice_lab import audio as A
from defence_studio.voice_lab import prosody as P
from defence_studio.voice_lab.common import VoiceLabError, canon, loads, new_id, now
from defence_studio.voice_lab.service import VoiceLabService

MAX_UPLOAD = int(os.environ.get("DEFENCE_STUDIO_VOICE_LAB_MAX_UPLOAD_BYTES", 2 * 1024 ** 3))
MEDIA = {"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg", "ogg": "audio/ogg", "json": "application/json"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsentBody(Strict):
    subject_name: str = Field(min_length=1, max_length=200)
    subject_role: str = Field(pattern="^(NARRATOR|REFERENCE_SPEAKER)$")
    scope: list[str] = Field(min_length=1, max_length=4)
    document_ref: str = Field(min_length=1, max_length=500)
    document_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    granted_at: str | None = None
    expires_at: str | None = None


class NarratorBody(Strict):
    display_name: str = Field(min_length=1, max_length=120)
    gender_label: str | None = Field(default=None, max_length=40)
    languages: list[str] = Field(default_factory=lambda: ["ta"], min_length=1, max_length=12)
    description: str = Field(default="", max_length=2000)
    consent_id: str | None = None


class NarratorPatch(Strict):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    gender_label: str | None = Field(default=None, max_length=40)
    languages: list[str] | None = None
    description: str | None = Field(default=None, max_length=2000)
    consent_id: str | None = None


class BaseVersionBody(Strict):
    model_id: str
    speaker: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    f0_median_hz: float | None = Field(default=None, gt=50, lt=500)
    f0_min_hz: float | None = Field(default=None, gt=50, lt=500)
    f0_max_hz: float | None = Field(default=None, gt=50, lt=600)
    note: str = Field(default="", max_length=500)
    activate: bool = True


class DatasetBody(Strict):
    narrator_id: str
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(default="ta", max_length=12)
    consent_id: str


class SegmentPatch(Strict):
    transcript: str | None = Field(default=None, max_length=2000)
    transcript_status: str | None = Field(default=None, pattern="^(NONE|DRAFT|REVIEWED)$")
    excluded: bool | None = None


class TrainingBody(Strict):
    narrator_id: str
    dataset_version_id: str
    model_id: str = "indic-parler-tts"
    target: str = Field(default="local", pattern="^(local|cloud)$")
    config: dict[str, Any] = Field(default_factory=dict)
    resume_from_job: str | None = None


class CursorTrainingBody(BaseModel):  # shape proposed by Agent B (coordination §18)
    identity: str
    consentRef: str | None = None
    segmentCount: int | None = None
    durationSec: float | None = None
    datasetVersionId: str | None = None
    modelId: str | None = None


class UrlReferenceBody(Strict):
    name: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=8, max_length=2000)
    authorization_ref: str = Field(min_length=1, max_length=500)


class StyleBody(Strict):
    reference_id: str
    name: str = Field(min_length=1, max_length=200)
    analysis_id: str | None = None


class LabelBody(Strict):
    feature_key: str = Field(pattern="^(emotion|delivery_label)$")
    value: dict[str, Any]


class GenerationBody(Strict):
    narrator_id: str
    text: str = Field(min_length=1, max_length=4000)
    language: str = "ta"
    model_version_id: str | None = None
    style_profile_id: str | None = None
    target_duration_ms: int | None = Field(default=None, ge=500, le=3_600_000)
    config: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class ScriptGenerationBody(BaseModel):
    """Script Studio contract. Accepts snake_case and Agent B's camelCase names."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    text: str | None = Field(default=None, alias="scriptText", max_length=4000)
    narrator_id: str | None = Field(default=None, alias="voiceId")
    style_profile_id: str | None = Field(default=None, alias="styleProfileId")
    model_version_id: str | None = None
    script_id: str | None = None
    chapter_id: str | None = Field(default=None, alias="chapterId")
    paragraph_id: str | None = None
    target_duration_ms: int | None = None
    timing_target_sec: float | None = Field(default=None, alias="timingTargetSec")
    language: str = "ta"
    config: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class BatchBody(Strict):
    script_id: str | None = None
    chapter_id: str | None = None
    narrator_id: str
    model_version_id: str | None = None
    style_profile_id: str | None = None
    language: str = "ta"
    config: dict[str, Any] = Field(default_factory=dict)
    paragraphs: list[dict[str, Any]] = Field(min_length=1, max_length=200)


class ProjectBody(Strict):
    name: str = Field(default="Untitled", max_length=200)
    sample_rate: int = 48000


class OpBody(Strict):
    op: dict[str, Any]
    expected_revision: int | None = None


class ExportBody(Strict):
    format: str = Field(default="wav", pattern="^(wav|flac|mp3|ogg)$")
    sample_rate: int | None = None
    channels: int = Field(default=1, ge=1, le=2)
    normalize_lufs: float | None = None
    t0_ms: int = Field(default=0, ge=0)
    t1_ms: int | None = None
    expected_revision: int | None = None


class ComparisonBody(Strict):
    tracks: list[dict[str, Any]] = Field(min_length=1, max_length=12)


class ReviewBody(Strict):
    artifact_id: str
    generation_id: str | None = None
    ratings: dict[str, int] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=4000)


def register_voice_lab_routes(app: FastAPI, require_owner, svc: VoiceLabService) -> None:
    P_ = "/api/voice-lab"

    def call(fn, *a, **k):
        try:
            return fn(*a, **k)
        except VoiceLabError as e:
            raise HTTPException(e.status_code, e.detail) from e

    async def save_upload(upload: UploadFile):
        name = os.path.basename(upload.filename or "upload")
        ext = os.path.splitext(name)[1].lower()
        if ext not in A.SUPPORTED_EXT:
            raise HTTPException(422, f"unsupported format {ext or '(none)'}")
        call(svc.ctx.require_space, 0)
        tmp = svc.ctx.tmp_path(ext)
        size = 0
        try:
            with open(tmp, "wb") as f:
                while chunk := await upload.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, f"upload larger than {MAX_UPLOAD} bytes")
                    f.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if size == 0:
            tmp.unlink(missing_ok=True)
            raise HTTPException(422, "empty upload")
        return tmp, name

    # ---------------------------------------------------------------- system
    @app.get(f"{P_}/system")
    def system(refresh: bool = False, owner_id: str = Depends(require_owner)):
        return call(svc.system, refresh)

    @app.get(f"{P_}/models")
    def models(owner_id: str = Depends(require_owner)):
        return {"models": svc.models()}

    @app.get(f"{P_}/models/{{model_id}}")
    def model(model_id: str, owner_id: str = Depends(require_owner)):
        m = next((m for m in svc.models() if m["model_id"] == model_id), None)
        if not m:
            raise HTTPException(404, "unknown model")
        return m

    @app.get(f"{P_}/licences")
    def licences(owner_id: str = Depends(require_owner)):
        with closing(svc.ctx.conn()) as c:
            rows = c.execute("SELECT * FROM vl_model_licences ORDER BY model_id").fetchall()
        return {"licences": [dict(r) | {"record": loads(r["record_json"])} for r in rows]}

    @app.get(f"{P_}/storage")
    def storage(owner_id: str = Depends(require_owner)):
        return svc.storage_report(owner_id)

    # -------------------------------------------------------------- consents
    @app.post(f"{P_}/consents", status_code=201)
    def create_consent(body: ConsentBody, owner_id: str = Depends(require_owner)):
        return call(svc.narrators.create_consent, owner_id, **body.model_dump())

    @app.get(f"{P_}/consents")
    def list_consents(owner_id: str = Depends(require_owner)):
        return {"consents": svc.narrators.list_consents(owner_id)}

    @app.post(f"{P_}/consents/{{cid}}/revoke")
    def revoke_consent(cid: str, owner_id: str = Depends(require_owner)):
        return call(svc.narrators.revoke_consent, cid, owner_id)

    # ------------------------------------------------------------- narrators
    @app.get(f"{P_}/narrators")
    def list_narrators(include_archived: bool = False, owner_id: str = Depends(require_owner)):
        return {"narrators": svc.narrators.list(owner_id, include_archived)}

    @app.post(f"{P_}/narrators", status_code=201)
    def create_narrator(body: NarratorBody, owner_id: str = Depends(require_owner)):
        return call(svc.narrators.create, owner_id, **body.model_dump())

    @app.get(f"{P_}/narrators/{{nid}}")
    def get_narrator(nid: str, owner_id: str = Depends(require_owner)):
        n = call(svc.narrators.get, nid, owner_id)
        n["versions"] = call(svc.narrators.versions, nid, owner_id)
        n["datasets"] = svc.datasets.list(owner_id, nid)
        n["training_jobs"] = svc.training.list(owner_id, nid)
        n["recent_generations"] = [{k: g[k] for k in ("generation_id", "status", "created_at", "style_profile_id")}
                                   for g in svc.generation.list(owner_id, nid, 20)]
        return n

    @app.patch(f"{P_}/narrators/{{nid}}")
    def patch_narrator(nid: str, body: NarratorPatch, owner_id: str = Depends(require_owner)):
        return call(svc.narrators.update, nid, owner_id, **body.model_dump(exclude_unset=True))

    @app.post(f"{P_}/narrators/{{nid}}/archive")
    def archive(nid: str, owner_id: str = Depends(require_owner)):
        return call(svc.narrators.set_status, nid, owner_id, "ARCHIVED")

    @app.post(f"{P_}/narrators/{{nid}}/restore")
    def restore(nid: str, owner_id: str = Depends(require_owner)):
        return call(svc.narrators.set_status, nid, owner_id, "ACTIVE")

    @app.get(f"{P_}/narrators/{{nid}}/versions")
    def versions(nid: str, owner_id: str = Depends(require_owner)):
        return {"versions": call(svc.narrators.versions, nid, owner_id)}

    @app.post(f"{P_}/narrators/{{nid}}/versions", status_code=201)
    def add_version(nid: str, body: BaseVersionBody, owner_id: str = Depends(require_owner)):
        rng = {k: v for k, v in body.model_dump().items() if k.startswith("f0_") and v}
        mvid = call(svc.narrators.add_base_version, nid, owner_id, model_id=body.model_id, speaker=body.speaker,
                    description=body.description, f0_range=rng or None, note=body.note)
        if body.activate:
            call(svc.narrators.activate, nid, owner_id, mvid)
        return call(svc.narrators.version, mvid, owner_id)

    @app.post(f"{P_}/narrators/{{nid}}/versions/{{mvid}}/activate")
    def activate(nid: str, mvid: str, owner_id: str = Depends(require_owner)):
        return call(svc.narrators.activate, nid, owner_id, mvid)

    # -------------------------------------------------------------- datasets
    @app.post(f"{P_}/datasets", status_code=201)
    def create_dataset(body: DatasetBody, owner_id: str = Depends(require_owner)):
        return call(svc.datasets.create, owner_id, **body.model_dump())

    @app.get(f"{P_}/datasets")
    def list_datasets(narrator_id: str | None = None, owner_id: str = Depends(require_owner)):
        return {"datasets": svc.datasets.list(owner_id, narrator_id)}

    @app.get(f"{P_}/datasets/{{did}}")
    def get_dataset(did: str, owner_id: str = Depends(require_owner)):
        return call(svc.datasets.get, did, owner_id)

    @app.get(f"{P_}/datasets/{{did}}/readiness")
    def readiness(did: str, owner_id: str = Depends(require_owner)):
        return call(svc.datasets.readiness, did, owner_id)

    @app.post(f"{P_}/datasets/{{did}}/files", status_code=202)
    async def upload_file(did: str, file: UploadFile = File(...), source_note: str = Form(...),
                          owner_id: str = Depends(require_owner)):
        call(svc.datasets._row, did, owner_id)
        tmp, name = await save_upload(file)
        try:
            res = call(svc.datasets.add_file, owner_id, did, temp_path=tmp, original_name=name, source_note=source_note)
        finally:
            tmp.unlink(missing_ok=True)
        res["workflow_job_id"] = svc.enqueue(owner_id, f"vl-seg-{res['file_id']}", "vl_dataset_segment", res["file_id"],
                                             f"Voice Lab segment {name}")
        return res

    @app.get(f"{P_}/datasets/{{did}}/segments")
    def segments(did: str, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                 owner_id: str = Depends(require_owner)):
        return call(svc.datasets.segments, did, owner_id, limit, offset)

    @app.patch(f"{P_}/segments/{{sid}}")
    def patch_segment(sid: str, body: SegmentPatch, owner_id: str = Depends(require_owner)):
        return call(svc.datasets.update_segment, owner_id, sid, **body.model_dump(exclude_unset=True))

    @app.post(f"{P_}/datasets/{{did}}/detect-duplicates")
    def dups(did: str, owner_id: str = Depends(require_owner)):
        return {"duplicates": call(svc.datasets.detect_duplicates, did, owner_id)}

    @app.post(f"{P_}/datasets/{{did}}/versions", status_code=201)
    def new_dataset_version(did: str, owner_id: str = Depends(require_owner)):
        return call(svc.datasets.create_version, owner_id, did)

    @app.get(f"{P_}/dataset-versions/{{vid}}")
    def get_dataset_version(vid: str, owner_id: str = Depends(require_owner)):
        return call(svc.datasets.version, vid, owner_id)

    @app.post(f"{P_}/dataset-versions/{{vid}}/approve")
    def approve_dataset_version(vid: str, owner_id: str = Depends(require_owner)):
        return call(svc.datasets.approve_version, owner_id, vid, owner_id)

    # -------------------------------------------------------------- training
    @app.post(f"{P_}/training-jobs/preflight")
    def preflight(body: TrainingBody, owner_id: str = Depends(require_owner)):
        return call(svc.training.preflight, owner_id, narrator_id=body.narrator_id,
                    dataset_version_id=body.dataset_version_id, model_id=body.model_id, target=body.target)

    @app.post(f"{P_}/training-jobs", status_code=202)
    def start_training(body: TrainingBody, idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
                       owner_id: str = Depends(require_owner)):
        return call(svc.training.start, owner_id, idempotency_key, narrator_id=body.narrator_id,
                    dataset_version_id=body.dataset_version_id, model_id=body.model_id, target=body.target,
                    config=body.config, resume_from_job=body.resume_from_job, enqueue=svc.enqueue)

    @app.post(f"{P_}/training", status_code=202)
    def cursor_training(body: CursorTrainingBody, owner_id: str = Depends(require_owner)):
        """Agent B's proposed shape. Maps onto the real gate; never fabricates a job."""
        narr = next((n for n in svc.narrators.list(owner_id) if body.identity in (n["narrator_id"], n["display_name"])), None)
        if narr is None:
            raise HTTPException(409, {"code": "TRAINING_BLOCKED", "blockers": [
                {"code": "NARRATOR_NOT_FOUND", "detail": f"no narrator {body.identity!r}; create it via POST /api/voice-lab/narrators"}]})
        if not body.datasetVersionId:
            hw = svc.system()
            raise HTTPException(409, {"code": "TRAINING_BLOCKED", "blockers": [
                {"code": "DATASET_VERSION_REQUIRED", "detail": "send datasetVersionId of an APPROVED dataset version "
                 "(use POST /api/voice-lab/training-jobs)"}] + [{"code": b, "detail": "hardware"} for b in hw["training_blockers"]]})
        return call(svc.training.start, owner_id, f"cursor-train-{narr['narrator_id']}-{body.datasetVersionId}",
                    narrator_id=narr["narrator_id"], dataset_version_id=body.datasetVersionId,
                    model_id=body.modelId or "indic-parler-tts", enqueue=svc.enqueue)

    @app.get(f"{P_}/training-jobs")
    def list_training(narrator_id: str | None = None, owner_id: str = Depends(require_owner)):
        return {"training_jobs": svc.training.list(owner_id, narrator_id)}

    @app.get(f"{P_}/training-jobs/{{tid}}")
    def get_training(tid: str, owner_id: str = Depends(require_owner)):
        return call(svc.training.get, tid, owner_id)

    @app.get(f"{P_}/training-jobs/{{tid}}/log")
    def training_log(tid: str, owner_id: str = Depends(require_owner)):
        return call(svc.training.log_tail, tid, owner_id)

    @app.post(f"{P_}/training-jobs/{{tid}}/cancel")
    def cancel_training(tid: str, owner_id: str = Depends(require_owner)):
        return call(svc.training.request, tid, owner_id, "cancel", svc.cancel_workflow)

    @app.post(f"{P_}/training-jobs/{{tid}}/pause")
    def pause_training(tid: str, owner_id: str = Depends(require_owner)):
        return call(svc.training.request, tid, owner_id, "pause", svc.cancel_workflow)

    # ------------------------------------------------------------ references
    @app.post(f"{P_}/references", status_code=202)
    async def upload_reference(file: UploadFile = File(...), name: str = Form(...), authorization_ref: str = Form(...),
                               owner_id: str = Depends(require_owner)):
        tmp, fname = await save_upload(file)
        try:
            ref = call(svc.references.create, owner_id, name=name, authorization_ref=authorization_ref, temp_path=tmp,
                       original_name=fname)
        finally:
            tmp.unlink(missing_ok=True)
        svc.enqueue(owner_id, f"vl-ref-{ref['reference_id']}", "vl_reference_analysis", ref["reference_id"],
                    f"Voice Lab reference {name}")
        return ref

    @app.post(f"{P_}/references/url", status_code=202)
    def url_reference(body: UrlReferenceBody, owner_id: str = Depends(require_owner)):
        ref = call(svc.references.create, owner_id, name=body.name, authorization_ref=body.authorization_ref,
                   source_url=body.source_url)
        svc.enqueue(owner_id, f"vl-ref-{ref['reference_id']}", "vl_reference_analysis", ref["reference_id"],
                    f"Voice Lab reference {body.name}")
        return ref

    @app.get(f"{P_}/references")
    def list_refs(owner_id: str = Depends(require_owner)):
        return {"references": svc.references.list(owner_id)}

    @app.get(f"{P_}/references/{{rid}}")
    def get_ref(rid: str, owner_id: str = Depends(require_owner)):
        return call(svc.references.get, rid, owner_id)

    @app.get(f"{P_}/references/{{rid}}/prosody")
    def prosody(rid: str, frames: bool = False, owner_id: str = Depends(require_owner)):
        return call(svc.references.analysis, owner_id, rid, None, frames)

    @app.post(f"{P_}/references/{{rid}}/retry", status_code=202)
    def retry_ref(rid: str, owner_id: str = Depends(require_owner)):
        return call(svc.retry, owner_id, "reference", rid)

    # ---------------------------------------------------------------- styles
    @app.post(f"{P_}/style-profiles", status_code=201)
    def create_style(body: StyleBody, owner_id: str = Depends(require_owner)):
        return call(svc.references.create_style, owner_id, **body.model_dump())

    @app.get(f"{P_}/style-profiles")
    def list_styles(owner_id: str = Depends(require_owner)):
        return {"style_profiles": svc.references.list_styles(owner_id)}

    @app.get(f"{P_}/style-profiles/{{sid}}")
    def get_style(sid: str, owner_id: str = Depends(require_owner)):
        return call(svc.references.style, sid, owner_id)

    @app.patch(f"{P_}/style-profiles/{{sid}}/labels")
    def label_style(sid: str, body: LabelBody, owner_id: str = Depends(require_owner)):
        return call(svc.references.review_label, owner_id, sid, owner_id, feature_key=body.feature_key, value=body.value)

    @app.post(f"{P_}/style-profiles/{{sid}}/approve")
    def approve_style(sid: str, owner_id: str = Depends(require_owner)):
        return call(svc.references.approve_style, owner_id, sid, owner_id)

    # ----------------------------------------------------------- generations
    @app.post(f"{P_}/generations", status_code=202)
    def create_generation(body: GenerationBody, owner_id: str = Depends(require_owner)):
        return call(svc.generation.create, owner_id, enqueue=svc.enqueue, **body.model_dump())

    @app.get(f"{P_}/generations")
    def list_generations(narrator_id: str | None = None, owner_id: str = Depends(require_owner)):
        return {"generations": svc.generation.list(owner_id, narrator_id)}

    @app.get(f"{P_}/generations/{{gid}}")
    def get_generation(gid: str, owner_id: str = Depends(require_owner)):
        return call(svc.generation.get, gid, owner_id)

    @app.post(f"{P_}/generations/{{gid}}/cancel")
    def cancel_generation(gid: str, owner_id: str = Depends(require_owner)):
        g = call(svc.generation.get, gid, owner_id)
        if g["status"] not in ("QUEUED", "RUNNING"):
            raise HTTPException(409, f"generation is {g['status']}")
        svc.cancel_workflow(g["workflow_job_id"], owner_id)
        if g["status"] == "QUEUED":
            svc.generation._set(gid, status="CANCELLED", progress_note="cancelled")
        return call(svc.generation.get, gid, owner_id)

    @app.post(f"{P_}/generations/{{gid}}/retry", status_code=202)
    def retry_generation(gid: str, owner_id: str = Depends(require_owner)):
        return call(svc.retry, owner_id, "generation", gid)

    # --------------------------------------------------------- Script Studio
    def _script(owner_id, b: ScriptGenerationBody):
        if not b.text or not b.narrator_id:
            raise HTTPException(422, "text (scriptText) and narrator_id (voiceId) are required")
        target = b.target_duration_ms or (int(b.timing_target_sec * 1000) if b.timing_target_sec else None)
        g = call(svc.generation.create, owner_id, narrator_id=b.narrator_id, text=b.text, language=b.language,
                 model_version_id=b.model_version_id, style_profile_id=b.style_profile_id, target_duration_ms=target,
                 config=b.config, force=b.force, enqueue=svc.enqueue,
                 script_ref={"script_id": b.script_id, "chapter_id": b.chapter_id, "paragraph_id": b.paragraph_id})
        return svc.generation.script_result(g)

    @app.post(f"{P_}/script-generation", status_code=202)
    def script_generation(body: ScriptGenerationBody, owner_id: str = Depends(require_owner)):
        return _script(owner_id, body)

    @app.post(f"{P_}/script-generation/batch", status_code=202)
    def script_batch(body: BatchBody, owner_id: str = Depends(require_owner)):
        out = []
        for p in body.paragraphs:
            b = ScriptGenerationBody(text=p.get("text"), narrator_id=body.narrator_id, style_profile_id=body.style_profile_id,
                                     model_version_id=body.model_version_id, script_id=body.script_id,
                                     chapter_id=body.chapter_id, paragraph_id=p.get("paragraph_id"),
                                     target_duration_ms=p.get("target_duration_ms"), language=body.language,
                                     config=body.config, force=bool(p.get("force")))
            try:
                out.append(_script(owner_id, b))
            except HTTPException as e:
                out.append({"paragraph_id": p.get("paragraph_id"), "status": "REJECTED", "error": e.detail})
        return {"items": out, "reused": sum(bool(i.get("reused")) for i in out),
                "queued": sum(i.get("status") == "QUEUED" and not i.get("reused") for i in out)}

    @app.get(f"{P_}/script-generation/{{gid}}")
    def script_status(gid: str, owner_id: str = Depends(require_owner)):
        return svc.generation.script_result(call(svc.generation.get, gid, owner_id))

    # ------------------------------------------------------------- artifacts
    @app.post(f"{P_}/artifacts", status_code=201)
    async def upload_artifact(file: UploadFile = File(...), owner_id: str = Depends(require_owner)):
        tmp, name = await save_upload(file)
        try:
            x = call(A.decode, tmp, 48000)
            return call(svc.ctx.register_audio, owner_id, x, 48000, kind="PROJECT_UPLOAD",
                        source={"original_name": name, "imported_at": now()})
        finally:
            tmp.unlink(missing_ok=True)

    @app.get(f"{P_}/artifacts/{{aid}}")
    def get_artifact(aid: str, owner_id: str = Depends(require_owner)):
        return call(svc.ctx.artifact, aid, owner_id)

    @app.get(f"{P_}/artifacts/{{aid}}/audio")
    def artifact_audio(aid: str, download: bool = False, owner_id: str = Depends(require_owner)):
        a = call(svc.ctx.artifact, aid, owner_id)
        path = call(svc.ctx.artifact_path, aid, owner_id)
        return FileResponse(path, media_type=MEDIA.get(a["format"], "application/octet-stream"),
                            filename=f"{aid}.{a['format']}" if download else None)

    @app.get(f"{P_}/artifacts/{{aid}}/waveform")
    def waveform(aid: str, px_per_s: int = Query(100, ge=1, le=2000), owner_id: str = Depends(require_owner)):
        x = call(A.decode, call(svc.ctx.artifact_path, aid, owner_id), 8000)
        return {"artifact_id": aid, **A.peaks(x, 8000, px_per_s)}

    @app.get(f"{P_}/artifacts/{{aid}}/pitch")
    def pitch(aid: str, step_ms: int = Query(20, ge=10, le=200), owner_id: str = Depends(require_owner)):
        x = call(A.decode, call(svc.ctx.artifact_path, aid, owner_id), P.SR)
        fr = P.frames(x)
        k = max(1, step_ms // P.HOP_MS)
        f0 = fr["f0_hz"][::k]
        return {"artifact_id": aid, "step_ms": k * P.HOP_MS,
                "f0_hz": [None if np.isnan(v) else round(float(v), 1) for v in f0],
                "energy_db": [round(float(v), 1) for v in fr["energy_db"][::k]],
                "method": "EXT-03 autocorrelation tracker"}

    @app.get(f"{P_}/artifacts/{{aid}}/analysis")
    def artifact_analysis(aid: str, owner_id: str = Depends(require_owner)):
        x = call(A.decode, call(svc.ctx.artifact_path, aid, owner_id), 48000)
        x16 = call(A.decode, call(svc.ctx.artifact_path, aid, owner_id), P.SR)
        an = P.analyse_signal(x16)
        return {"artifact_id": aid, "quality": A.quality(x, 48000), "loudness": A.loudness(x, 48000),
                "prosody": {"summary": an["summary"],
                            "phrases": [{k: p[k] for k in ("t0_ms", "t1_ms", "pause_after_ms", "rel_median_st")}
                                        for p in an["phrases"]]}}

    @app.post(f"{P_}/artifacts/{{aid}}/approve")
    def approve_artifact(aid: str, owner_id: str = Depends(require_owner)):
        call(svc.ctx.artifact, aid, owner_id)
        with svc.ctx.tx() as c:
            c.execute("UPDATE vl_audio_artifacts SET approved=1 WHERE artifact_id=?", (aid,))
        return call(svc.ctx.artifact, aid, owner_id)

    @app.get(f"{P_}/compare")
    def compare(artifact_ids: str = Query(..., min_length=3), owner_id: str = Depends(require_owner)):
        ids = [i for i in artifact_ids.split(",") if i][:8]
        items = []
        for aid in ids:
            x16 = call(A.decode, call(svc.ctx.artifact_path, aid, owner_id), P.SR)
            an = P.analyse_signal(x16)
            s = an["summary"] or {}
            items.append({"artifact_id": aid, "duration_ms": an["total_ms"], "summary": s,
                          "phrase_starts_ms": [p["t0_ms"] for p in an["phrases"]],
                          "pauses_ms": [p["pause_after_ms"] for p in an["phrases"][:-1]],
                          "relative_pitch_st": [p["rel_median_st"] for p in an["phrases"]]})
        base = items[0]
        for it in items[1:]:
            it["vs_first"] = {"duration_diff_ms": it["duration_ms"] - base["duration_ms"],
                              "phrase_count_diff": len(it["phrase_starts_ms"]) - len(base["phrase_starts_ms"])}
            if len(it["phrase_starts_ms"]) == len(base["phrase_starts_ms"]):
                d = [abs(a - b) for a, b in zip(it["phrase_starts_ms"], base["phrase_starts_ms"])]
                it["vs_first"]["phrase_start_error_ms"] = {"mean": round(sum(d) / len(d), 1), "max": max(d)}
        return {"items": items}

    # -------------------------------------------------------------- projects
    @app.get(f"{P_}/projects")
    def list_projects(owner_id: str = Depends(require_owner)):
        return {"projects": svc.projects.list(owner_id)}

    @app.post(f"{P_}/projects", status_code=201)
    def create_project(body: ProjectBody, owner_id: str = Depends(require_owner)):
        return call(svc.projects.create, owner_id, **body.model_dump())

    @app.get(f"{P_}/projects/{{pid}}")
    def get_project(pid: str, owner_id: str = Depends(require_owner)):
        return call(svc.projects.doc, pid, owner_id)

    @app.post(f"{P_}/projects/{{pid}}/ops")
    def project_op(pid: str, body: OpBody, owner_id: str = Depends(require_owner)):
        return call(svc.projects.apply, owner_id, pid, body.op, body.expected_revision)

    @app.post(f"{P_}/projects/{{pid}}/undo")
    def undo(pid: str, owner_id: str = Depends(require_owner)):
        return call(svc.projects.undo, owner_id, pid)

    @app.post(f"{P_}/projects/{{pid}}/redo")
    def redo(pid: str, owner_id: str = Depends(require_owner)):
        return call(svc.projects.redo, owner_id, pid)

    @app.get(f"{P_}/projects/{{pid}}/history")
    def history(pid: str, owner_id: str = Depends(require_owner)):
        return {"operations": call(svc.projects.history, owner_id, pid)}

    @app.post(f"{P_}/projects/{{pid}}/export", status_code=201)
    def export(pid: str, body: ExportBody, owner_id: str = Depends(require_owner)):
        return call(svc.projects.export, owner_id, pid, fmt=body.format, sample_rate=body.sample_rate,
                    channels=body.channels, normalize_lufs=body.normalize_lufs, t0_ms=body.t0_ms, t1_ms=body.t1_ms,
                    expected_revision=body.expected_revision)

    @app.post(f"{P_}/projects/{{pid}}/comparison")
    def load_comparison(pid: str, body: ComparisonBody, owner_id: str = Depends(require_owner)):
        """Add one track per item: {role, name?, artifact_id | generation_id | reference_id}."""
        doc = call(svc.projects.doc, pid, owner_id)
        for item in body.tracks:
            aid = item.get("artifact_id")
            if not aid and item.get("generation_id"):
                aid = (call(svc.generation.get, item["generation_id"], owner_id).get("audio") or {}).get("artifact_id")
            if not aid and item.get("reference_id"):
                aid = call(svc.references.get, item["reference_id"], owner_id).get("speech_artifact_id")
            if not aid:
                raise HTTPException(409, f"no audio for {item}")
            r = call(svc.projects.apply, owner_id, pid, {"type": "add_track", "role": item.get("role", "other"),
                                                         "name": item.get("name") or item.get("role", "track")})
            tid = r["result"]["track_id"]
            doc = call(svc.projects.apply, owner_id, pid, {"type": "add_clip", "track_id": tid, "artifact_id": aid,
                                                           "start_ms": int(item.get("start_ms", 0))})["doc"]
        return doc

    # --------------------------------------------------------------- reviews
    @app.post(f"{P_}/reviews", status_code=201)
    def create_review(body: ReviewBody, owner_id: str = Depends(require_owner)):
        call(svc.ctx.artifact, body.artifact_id, owner_id)
        if any(not (1 <= v <= 5) for v in body.ratings.values()):
            raise HTTPException(422, "ratings must be 1..5")
        g = call(svc.generation.get, body.generation_id, owner_id) if body.generation_id else None
        rid = new_id("rev")
        with svc.ctx.tx() as c:
            c.execute("INSERT INTO vl_quality_reviews VALUES(?,?,?,?,?,?,?,?,?)",
                      (rid, owner_id, body.artifact_id, body.generation_id, g["model_version_id"] if g else None,
                       g["narrator_id"] if g else None, canon(body.ratings), body.notes, now()))
        return {"review_id": rid, "artifact_id": body.artifact_id, "model_version_id": g["model_version_id"] if g else None,
                "ratings": body.ratings, "notes": body.notes}

    @app.get(f"{P_}/reviews")
    def list_reviews(artifact_id: str | None = None, model_version_id: str | None = None,
                     owner_id: str = Depends(require_owner)):
        q, a = "SELECT * FROM vl_quality_reviews WHERE owner_id=?", [owner_id]
        if artifact_id:
            q += " AND artifact_id=?"; a.append(artifact_id)
        if model_version_id:
            q += " AND model_version_id=?"; a.append(model_version_id)
        with closing(svc.ctx.conn()) as c:
            rows = c.execute(q + " ORDER BY created_at DESC", a).fetchall()
        return {"reviews": [dict(r) | {"ratings": loads(r["ratings_json"])} for r in rows]}
