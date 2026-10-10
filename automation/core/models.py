"""Typed domain models shared across NDF layers.

Every piece of data that crosses a layer boundary is one of these dataclasses,
never a loose dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(slots=True)
class VersionInfo:
    """The platform version, the single source of truth rendered into VERSION.md."""

    major: int = 0
    minor: int = 1
    patch: int = 0
    build_number: int = 0
    build_date: str = ""          # ISO date, e.g. 2026-06-27
    release_name: str = ""
    developer: str = ""
    git_commit: str = ""
    branch: str = ""

    @property
    def semver(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def full(self) -> str:
        return f"{self.semver}+build.{self.build_number}"


@dataclass(slots=True)
class ChangelogEntry:
    """A single categorized change."""

    section: str            # one of the configured changelog sections
    summary: str
    scope: str = ""
    commit_hash: str = ""
    date: str = ""


@dataclass(slots=True)
class DocRecord:
    """An indexed documentation file."""

    module: str
    document: str
    path: str               # repo-relative
    version: str = ""
    status: str = "Draft"
    owner: str = ""
    updated_date: str = ""


@dataclass(slots=True)
class AdrRecord:
    """An Architecture Decision Record entry."""

    number: int
    title: str
    status: str = "Proposed"
    date: str = ""
    path: str = ""

    @property
    def identifier(self) -> str:
        return f"ADR-{self.number:03d}"


@dataclass(slots=True)
class BusinessRule:
    """A registered business rule with a permanent identifier."""

    identifier: str         # e.g. PR-BR-001
    name: str
    module: str
    version: str = "1.0"
    priority: str = "Medium"
    status: str = "Active"
    dependencies: list[str] = field(default_factory=list)
    description: str = ""
    path: str = ""


@dataclass(slots=True)
class ModuleStatus:
    """Completion/status of a platform module, for the project dashboard."""

    name: str
    status: str = "Planned"
    completion_pct: int = 0
    owner: str = ""
    milestone: str = ""


@dataclass(slots=True)
class AiArtifact:
    """An AI-generated documentation artifact retained for provenance."""

    identifier: str
    source: str             # ChatGPT | Claude
    kind: str               # prompt | output
    title: str = ""
    approval_status: str = "Pending"   # Pending | Approved | Rejected
    revision: int = 1
    date: str = ""
    path: str = ""


@dataclass(slots=True)
class HealthFinding:
    """A single repository-health observation."""

    category: str           # large | duplicate | broken-link | missing-doc | orphan
    target: str
    detail: str = ""
    severity: str = "info"  # info | warning | error


def today_iso() -> str:
    """Convenience for tests/builders needing a default date string."""
    return date.today().isoformat()
