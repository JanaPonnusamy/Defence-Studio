"""Pure evaluator; approval is supplied by the engine's persisted review."""
from .contracts import *

def evaluate_effective_rights(record: RightsRecord, context: UseContext, *, rights_revision: int=1, review: ReviewEvent|None=None) -> Evaluation:
    revision(rights_revision)
    blockers=[]
    if record.state != RightsState.PENDING_REVIEW or review is None or review.kind != "rights" or review.decision != "APPROVED" or review.revision != rights_revision or review.content_digest != digest(record):
        blockers.append("RIGHTS_NOT_APPROVED")
    if review is not None and instant(review.reviewed_at)>instant(context.evaluated_at): blockers.append("REVIEW_AFTER_EVALUATION")
    if not record.rights_basis or not record.licence_name or not record.evidence_notes:
        blockers.append("INCOMPLETE_RIGHTS_EVIDENCE")
    for field in ("commercial","modification","distribution"):
        if getattr(context,field) and getattr(record,field) is not True: blockers.append(field.upper()+"_NOT_PERMITTED")
    if record.attribution_required is None: blockers.append("ATTRIBUTION_REQUIREMENT_UNKNOWN")
    if record.attribution_required and (not record.attribution_text or context.attribution_text!=record.attribution_text): blockers.append("ATTRIBUTION_MISSING_OR_MISMATCHED")
    if record.territories and context.territory not in record.territories: blockers.append("TERRITORY_RESTRICTED")
    if record.valid_from and instant(context.evaluated_at)<instant(record.valid_from): blockers.append("RIGHTS_NOT_YET_VALID")
    if record.valid_until and instant(context.evaluated_at)>=instant(record.valid_until): blockers.append("RIGHTS_EXPIRED")
    return Evaluation(not blockers,tuple(blockers),(),("REVIEW_DECLARED_RIGHTS",) if blockers else (),rights_revision,digest(record),context.evaluated_at)
