"""Changelog generation from git history + the configured commit→section mapping."""

from __future__ import annotations

import re

from ..builders.changelog_builder import ChangelogBuilder
from ..core.config import NDFConfig
from ..core.logging import get_logger
from ..core.models import ChangelogEntry
from ..core.paths import PathResolver
from ..core.result import CommandResult

# Conventional commit subject: type(scope)!: summary
_CONVENTIONAL = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s*(?P<summary>.+)$")


class ChangelogService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, git, clock) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._git = git
        self._clock = clock
        self._sections = config.changelog_sections or [
            "Added", "Changed", "Fixed", "Removed", "Deprecated", "Security", "Performance",
        ]
        self._mapping = {k.lower(): v for k, v in config.commit.get("mapping", {}).items()}
        self._default_section = config.commit.get("default_section", "Changed")
        self._builder = ChangelogBuilder(self._sections)
        self._log = get_logger("services.changelog")

    def collect_entries(self, since_ref: str = "") -> list[ChangelogEntry]:
        """Build categorized entries from commit subjects since ``since_ref``."""
        entries: list[ChangelogEntry] = []
        today = self._clock.today_iso()
        for raw in self._git.log_since(since_ref):
            commit_hash, _, subject = raw.partition("\t")
            section, scope, summary = self._classify(subject)
            entries.append(
                ChangelogEntry(
                    section=section,
                    summary=summary,
                    scope=scope,
                    commit_hash=commit_hash.strip(),
                    date=today,
                )
            )
        return entries

    def _classify(self, subject: str) -> tuple[str, str, str]:
        match = _CONVENTIONAL.match(subject.strip())
        if not match:
            return self._default_section, "", subject.strip()
        ctype = match.group("type").lower()
        section = self._mapping.get(ctype, self._default_section)
        if match.group("bang"):
            section = self._mapping.get("breaking", section)
        return section, match.group("scope") or "", match.group("summary").strip()

    def build(self, version: str) -> CommandResult:
        """Regenerate CHANGELOG.md, prepending a release block for ``version``."""
        since = self._git.latest_tag()
        entries = self.collect_entries(since)
        block = self._builder.render_release_block(version, self._clock.today_iso(), entries)

        existing_blocks = self._existing_release_blocks()
        # Replace an existing block for this version, else prepend.
        header = f"## [{version}]"
        existing_blocks = [b for b in existing_blocks if not b.startswith(header)]
        all_blocks = [block, *existing_blocks]
        content = self._builder.render_full(all_blocks)
        rel = self._fs.write_text(self._paths.changelog_file, content)
        self._log.info("changelog rebuilt for %s (%d entries)", version, len(entries))
        return CommandResult.success(
            f"CHANGELOG.md updated for {version} ({len(entries)} change(s)).",
            changed_paths=[rel],
            data={"entries": len(entries)},
        )

    def _existing_release_blocks(self) -> list[str]:
        path = self._paths.changelog_file
        if not self._fs.exists(path):
            return []
        text = self._fs.read_text(path)
        # Split on release headings, keeping them.
        parts = re.split(r"(?=^## \[)", text, flags=re.MULTILINE)
        return [p.strip() for p in parts if p.strip().startswith("## [")]
