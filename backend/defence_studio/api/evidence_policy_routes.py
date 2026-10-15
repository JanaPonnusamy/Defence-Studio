"""Automatic evidence policy routes (owner-authenticated; same job ownership rules as /package)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from defence_studio.services.evidence_policy import EvidencePolicyService
from defence_studio.services.review import ReviewNotFound, ReviewValidationError


def register_evidence_policy_routes(app: FastAPI, require_owner, policy: EvidencePolicyService) -> None:
    @app.post("/api/jobs/{job_id}/evidence/policy/run")
    def run_policy(job_id: str, owner_id: str = Depends(require_owner)):
        try:
            return policy.run(job_id, owner_id)
        except ReviewNotFound as exc:
            raise HTTPException(404, str(exc)) from None
        except ReviewValidationError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/jobs/{job_id}/evidence/policy")
    def latest_policy(job_id: str, owner_id: str = Depends(require_owner)):
        try:
            result = policy.latest(job_id, owner_id)
        except ReviewNotFound as exc:
            raise HTTPException(404, str(exc)) from None
        if result is None:
            raise HTTPException(404, "No evidence policy run recorded for this job")
        return result
