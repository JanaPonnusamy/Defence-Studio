"""Trusted offline input boundary for future generation; deliberately no route."""
from defence_studio.contracts.evidence import digest
from defence_studio.contracts.script import ScriptRequestV1
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.review import StaleRevision


def prepare_request(evidence: EvidenceService, job_id: str, owner_id: str, payload: dict) -> dict:
    request = ScriptRequestV1.model_validate(payload)
    package = evidence.export(job_id, owner_id)  # validates ledger, current reviews and all bytes
    if digest(package) != request.package_sha256:
        raise StaleRevision("Script request must pin the current approved package bytes")
    return {"request": request.model_dump(), "request_sha256": digest(request.model_dump()),
            "evidence": package, "eligible_claim_ids": package["eligible_claim_ids"],
            "requirements": ["Original narration; preserve uncertainty, negations and technical designations.",
                             "Every statement cites approved claim IDs; rejected claims cannot enter narration.",
                             "Tamil and English outputs require human language and source-faithfulness review.",
                             "Record actual model, token use, retries and measured INR text cost; report other costs separately."]}
