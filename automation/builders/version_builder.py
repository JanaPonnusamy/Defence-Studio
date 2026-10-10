"""Renders a :class:`VersionInfo` into the ``VERSION.md`` document."""

from __future__ import annotations

from ..core.models import VersionInfo
from ..templates.loader import render_template


class VersionBuilder:
    def render(self, version: VersionInfo) -> str:
        return render_template(
            "version.md.tmpl",
            {
                "semver": version.semver,
                "major": str(version.major),
                "minor": str(version.minor),
                "patch": str(version.patch),
                "build_number": str(version.build_number),
                "build_date": version.build_date,
                "release_name": version.release_name or "—",
                "developer": version.developer or "—",
                "git_commit": version.git_commit or "—",
                "branch": version.branch or "—",
            },
        )
