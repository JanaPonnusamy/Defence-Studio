"""GitHub synchronization: detect → group → standardized commit → push → optional tag."""

from __future__ import annotations

from ..core.config import NDFConfig
from ..core.logging import get_logger
from ..core.paths import PathResolver
from ..core.result import CommandResult


class GitHubSyncService:
    def __init__(self, config: NDFConfig, paths: PathResolver, git, version_service) -> None:
        self._config = config
        self._paths = paths
        self._git = git
        self._versions = version_service
        self._groups = config.github.get("groups", {})
        self._default_group = config.github.get("default_group", "chore")
        self._log = get_logger("services.github")

    def sync(self, create_tag: bool = False, push: bool = True) -> CommandResult:
        if not self._git.is_repository():
            return CommandResult.failure("Not inside a git repository.")
        branch = self._git.current_branch()
        changes = self._git.changes()
        if not changes:
            return CommandResult.success("Working tree clean — nothing to sync.")

        grouped = self._group(changes)
        commits: list[str] = []
        for group_key, paths in grouped.items():
            self._git.add(paths)
            message = self._message(group_key, paths)
            self._git.commit(message)
            commits.append(message.splitlines()[0])
            self._log.info("committed group '%s' (%d file(s))", group_key, len(paths))

        pushed = False
        if push:
            remote = self._config.github.get("remote", "origin")
            self._git.push(remote, branch)
            pushed = True

        tag_note = ""
        if create_tag:
            tag_note = self._tag(branch)

        message = f"Synced {len(commits)} commit group(s) on '{branch}'."
        if pushed:
            message += " Pushed."
        if tag_note:
            message += f" {tag_note}"
        return CommandResult.success(message, changed_paths=[c for c in commits],
                                    data={"groups": list(grouped.keys()), "pushed": pushed})

    def _group(self, changes) -> dict[str, list[str]]:
        """Group changed paths by their configured top-level area."""
        grouped: dict[str, list[str]] = {}
        for change in changes:
            key = self._group_key(change.path)
            grouped.setdefault(key, []).append(change.path)
        # stable ordering by group key
        return dict(sorted(grouped.items()))

    def _group_key(self, path: str) -> str:
        for key, prefixes in self._groups.items():
            for prefix in prefixes:
                if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                    return key
        return self._default_group

    def _message(self, group_key: str, paths: list[str]) -> str:
        type_for = self._config.github.get("group_types", {})
        ctype = type_for.get(group_key, "chore")
        summary = f"sync {len(paths)} file(s) in {group_key}"
        body = "\n".join(f"- {p}" for p in paths)
        return f"{ctype}({group_key}): {summary}\n\n{body}"

    def _tag(self, branch: str) -> str:
        version = self._versions.current()
        prefix = self._config.github.get("tag_prefix", "v")
        tag = f"{prefix}{version.semver}"
        if tag in self._git.list_tags():
            return f"Tag {tag} already exists; skipped."
        self._git.tag(tag, message=f"Release {version.semver}")
        if self._config.github.get("push_tags", True):
            self._git.push_tag(self._config.github.get("remote", "origin"), tag)
        return f"Tag {tag} created."
