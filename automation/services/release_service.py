"""Release notes generation + changelog refresh + optional tagging for a milestone."""

from __future__ import annotations

from ..builders.release_notes_builder import ReleaseNotesBuilder
from ..core.config import NDFConfig
from ..core.logging import get_logger
from ..core.paths import PathResolver
from ..core.result import CommandResult


class ReleaseService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, git, clock,
                 version_service, changelog_service) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._git = git
        self._clock = clock
        self._versions = version_service
        self._changelog = changelog_service
        self._builder = ReleaseNotesBuilder()
        self._log = get_logger("services.release")

    def build(self, create_tag: bool = False) -> CommandResult:
        """Generate RELEASE_NOTES.md + refresh CHANGELOG.md for the current version."""
        version = self._versions.current()
        changed: list[str] = []

        changelog_result = self._changelog.build(version.semver)
        changed.extend(changelog_result.changed_paths)

        entries = self._changelog.collect_entries(self._git.latest_tag())
        notes = self._builder.render(version, self._clock.today_iso(), entries)
        changed.append(self._fs.write_text(self._paths.release_notes_file, notes))

        tag_message = ""
        if create_tag:
            tag_message = self._create_tag(version.semver)

        self._log.info("release built for %s (tag=%s)", version.semver, create_tag)
        message = f"Release {version.semver} built ({len(entries)} change(s))."
        if tag_message:
            message += f" {tag_message}"
        return CommandResult.success(message, changed_paths=changed,
                                    data={"version": version.semver})

    def _create_tag(self, semver: str) -> str:
        prefix = self._config.github.get("tag_prefix", "v")
        tag = f"{prefix}{semver}"
        if tag in self._git.list_tags():
            return f"Tag {tag} already exists; skipped."
        self._git.tag(tag, message=f"Release {semver}")
        return f"Tag {tag} created."
