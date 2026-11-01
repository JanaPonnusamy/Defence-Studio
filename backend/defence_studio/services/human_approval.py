"""Human-only production approval (owner decision C, 2026-11-01).

Automated validation may check, measure, recommend and list what is missing. It must never
approve an evidence package or a script. Every approval goes through an authenticated human
request (the API's bearer-token subject); this module is the single place that says which
actor identities are automated and which approval records count as human.

Legacy rows written before this decision (`reviewer_id` AUTOMATED_POLICY / AUTOMATED_VALIDATION)
stay in the append-only ledgers as history, but never make anything eligible.
"""

from __future__ import annotations

from defence_studio.services.review import ReviewError

AUTOMATED_POLICY = "AUTOMATED_POLICY"
AUTOMATED_VALIDATION = "AUTOMATED_VALIDATION"
AUTOMATED_ACTORS = frozenset({AUTOMATED_POLICY, AUTOMATED_VALIDATION, "SYSTEM", "AUTOMATION", "WORKER"})
# Worker and automation identities used inside the process (never issued as bearer subjects).
AUTOMATED_PREFIXES = ("automated", "automation", "system:", "worker-", "research-worker", "policy-")

# SQL fragment: ledger rows that count as a human approval.
HUMAN_REVIEWER_SQL = f"reviewer_id NOT IN ('{AUTOMATED_POLICY}', '{AUTOMATED_VALIDATION}')"
APPROVAL_POLICY_VERSION = "human-only-approval.v1"


class HumanApprovalRequired(ReviewError):
    """An automated or unidentified actor attempted a production approval (HTTP 403)."""

    status_code = 403


def is_automated_actor(actor) -> bool:
    if not isinstance(actor, str) or not actor.strip():
        return True
    value = actor.strip()
    return value.upper() in AUTOMATED_ACTORS or value.lower().startswith(AUTOMATED_PREFIXES)


def require_human(actor) -> str:
    """Return the actor id, or raise when it is not an identifiable human user."""
    if is_automated_actor(actor):
        raise HumanApprovalRequired(
            "Production approval requires an authenticated human reviewer; automated actors "
            "may only validate and recommend")
    return actor.strip()


def is_human_record(reviewer_id) -> bool:
    return not is_automated_actor(reviewer_id)
