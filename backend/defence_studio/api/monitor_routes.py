"""Owner-authenticated research monitor / evidence explorer routes (read-only)."""

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response

from defence_studio.services.research_monitor import ResearchMonitorService
from defence_studio.services.review import ReviewNotFound, ReviewValidationError


def register_monitor_routes(app: FastAPI, require_owner, monitor: ResearchMonitorService) -> None:
    def guarded(fn, *args):
        try:
            return fn(*args)
        except ReviewNotFound as exc:
            raise HTTPException(404, str(exc)) from None
        except ReviewValidationError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/jobs/{job_id}/monitor")
    def job_monitor(job_id: str, owner_id: str = Depends(require_owner)):
        return guarded(monitor.monitor, job_id, owner_id)

    @app.get("/api/jobs/{job_id}/evidence/explorer")
    def explorer(job_id: str, owner_id: str = Depends(require_owner)):
        return guarded(monitor.explorer, job_id, owner_id)

    @app.get("/api/jobs/{job_id}/evidence/explorer/sources/{source_id}")
    def explorer_source(job_id: str, source_id: str, owner_id: str = Depends(require_owner)):
        return guarded(monitor.source_detail, job_id, owner_id, source_id)

    @app.get("/api/jobs/{job_id}/evidence/report.{fmt}")
    def report(job_id: str, fmt: str, owner_id: str = Depends(require_owner)):
        if fmt not in ("json", "csv"):
            raise HTTPException(404, "Unsupported report format")
        body, media, name = guarded(monitor.export, job_id, owner_id, fmt)
        return Response(body, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})
