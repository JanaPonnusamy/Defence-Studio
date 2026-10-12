"""Backward-compatibility shim. The provider formerly called "local-procedural" is a
PLACEHOLDER renderer (not AI); see app/generation/placeholder_procedural.py."""
from __future__ import annotations

from app.generation.placeholder_procedural import PlaceholderProceduralProvider

LocalProceduralProvider = PlaceholderProceduralProvider  # deprecated alias

__all__ = ["LocalProceduralProvider", "PlaceholderProceduralProvider"]
