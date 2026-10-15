"""Claim fact-check and media-context engines.

Both are deterministic and reusable without the HTTP app. They do not treat
a copied passage, a familiar publisher, or a genuine file as proof of a claim.
"""

from defence_studio.verification.claims import verify_claims
from defence_studio.verification.media import verify_media
from defence_studio.verification.package import build_evidence_package
from defence_studio.verification.providers import capability_registry, select_provider

__all__ = [
    "verify_claims",
    "verify_media",
    "build_evidence_package",
    "capability_registry",
    "select_provider",
]
