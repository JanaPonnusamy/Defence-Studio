"""Renders categorized changelog entries (Keep a Changelog style)."""

from __future__ import annotations

from ..core.models import ChangelogEntry


class ChangelogBuilder:
    HEADER = (
        "# Changelog\n\n"
        "All notable changes to the NEXORA platform are documented here.\n"
        "Generated and maintained by the NEXORA Development Framework.\n"
    )

    def __init__(self, sections: list[str]) -> None:
        self._sections = sections

    def render_release_block(
        self, version: str, date: str, entries: list[ChangelogEntry]
    ) -> str:
        """Render a single release block (heading + sections)."""
        lines = [f"## [{version}] - {date}", ""]
        grouped = {section: [] for section in self._sections}
        for entry in entries:
            grouped.setdefault(entry.section, []).append(entry)

        any_written = False
        for section in self._sections:
            items = grouped.get(section, [])
            if not items:
                continue
            any_written = True
            lines.append(f"### {section}")
            for item in items:
                scope = f"**{item.scope}**: " if item.scope else ""
                suffix = f" ({item.commit_hash})" if item.commit_hash else ""
                lines.append(f"- {scope}{item.summary}{suffix}")
            lines.append("")
        if not any_written:
            lines.append("_No categorized changes recorded._")
            lines.append("")
        return "\n".join(lines)

    def render_full(self, release_blocks: list[str]) -> str:
        return self.HEADER + "\n" + "\n".join(release_blocks).rstrip() + "\n"
