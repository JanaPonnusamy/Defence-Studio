"""Script Studio routes (owner-authenticated). Errors map through the app's ReviewError handler.

Every write creates a new revision; none approves. Approval stays on
``POST /api/jobs/{job_id}/script/revisions/{language}/{version}/approve`` (human-only)."""

from __future__ import annotations

from fastapi import Body, Depends, FastAPI, Header

from defence_studio.services.script_studio import ScriptStudioService, load_chapter_templates


def register_script_studio_routes(app: FastAPI, require_owner, studio: ScriptStudioService) -> None:
    @app.get("/api/script/chapter-templates")
    def chapter_templates(owner_id: str = Depends(require_owner)):
        return load_chapter_templates()

    @app.get("/api/jobs/{job_id}/script/revisions/{language}/{version}/chapters")
    def chapters(job_id: str, language: str, version: int, owner_id: str = Depends(require_owner)):
        return studio.chapters(job_id, owner_id, language, version)

    @app.post("/api/jobs/{job_id}/script/revisions/{language}/{version}/edit", status_code=201)
    def edit(job_id: str, language: str, version: int, body: dict = Body(...),
             idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
             owner_id: str = Depends(require_owner)):
        return studio.edit(job_id, owner_id, language, version, body, idempotency_key)

    @app.post("/api/jobs/{job_id}/script/revisions/{language}/{version}/sections/{section_id}/regenerate",
              status_code=201)
    def regenerate(job_id: str, language: str, version: int, section_id: str, body: dict = Body(default={}),
                   idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
                   owner_id: str = Depends(require_owner)):
        return studio.regenerate_section(job_id, owner_id, language, version, section_id, idempotency_key,
                                         str((body or {}).get("instruction") or ""))
