"""Test helper: the human evidence-package approval that decision C (2026-11-01) requires.

Automated policy runs only recommend; tests that exercise generation must approve as a person."""

from defence_studio.services.evidence import EvidenceService


def approve_package_as_human(db, root, job, owner, note="Reviewed the evidence package as a human", key=None):
    service = EvidenceService(db, root)
    revision = service.draft(job, owner)["revision"]
    return service.approve(job, owner, revision, note, key or f"human-approve-{revision[:16]}")
