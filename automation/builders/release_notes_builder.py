"""Renders RELEASE_NOTES.md from a version + categorized entries."""

from __future__ import annotations

from ..core.models import ChangelogEntry, VersionInfo
from ..templates.loader import render_template


class ReleaseNotesBuilder:
    def render(
        self, version: VersionInfo, date: str, entries: list[ChangelogEntry]
    ) -> str:
        highlights = self._highlights(entries)
        changes = self._changes(entries)
        return render_template(
            "release_notes.md.tmpl",
            {
                "version": version.semver,
                "release_name": version.release_name or "—",
                "date": date,
                "highlights": highlights,
                "changes": changes,
            },
        )

    def _highlights(self, entries: list[ChangelogEntry]) -> str:
        added = [e for e in entries if e.section == "Added"]
        if not added:
            return "_This release contains maintenance and internal changes._"
        lines = ["**Highlights:**", ""]
        for entry in added[:5]:
            lines.append(f"- {entry.summary}")
        return "\n".join(lines)

    def _changes(self, entries: list[ChangelogEntry]) -> str:
        if not entries:
            return "_No categorized changes recorded._"
        lines: list[str] = []
        sections: list[str] = []
        for entry in entries:
            if entry.section not in sections:
                sections.append(entry.section)
        for section in sections:
            lines.append(f"**{section}**")
            for entry in entries:
                if entry.section == section:
                    lines.append(f"- {entry.summary}")
            lines.append("")
        return "\n".join(lines).rstrip()
