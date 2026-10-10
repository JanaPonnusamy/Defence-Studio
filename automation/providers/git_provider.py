"""Git provider — wraps the local ``git`` CLI behind a typed interface.

Safety policy: this provider never force-pushes and never bypasses hooks
(``--no-verify``) or signing. Every mutating invocation is logged at INFO.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.exceptions import GitError
from ..core.logging import get_logger


@dataclass(slots=True)
class GitChange:
    """A single porcelain change entry."""

    status: str   # two-char XY porcelain code, e.g. ' M', '??', 'A '
    path: str     # repo-relative, forward slashes


class GitProvider:
    """Thin, safe wrapper over the git command line."""

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root.resolve()
        self._log = get_logger("providers.git")

    # ---- internal --------------------------------------------------------
    def _run(self, args: list[str], *, mutating: bool = False) -> str:
        cmd = ["git", *args]
        if mutating:
            self._log.info("git %s", " ".join(args))
        else:
            self._log.debug("git %s", " ".join(args))
        try:
            proc = subprocess.run(
                cmd, cwd=self._root, capture_output=True, text=True, check=True
            )
        except FileNotFoundError as exc:
            raise GitError("git executable not found on PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise GitError(f"git {' '.join(args)} failed: {detail}") from exc
        return proc.stdout.strip()

    # ---- queries ---------------------------------------------------------
    def is_repository(self) -> bool:
        try:
            return self._run(["rev-parse", "--is-inside-work-tree"]) == "true"
        except GitError:
            return False

    def current_branch(self) -> str:
        branch = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        if branch == "HEAD":
            raise GitError("Repository is in a detached HEAD state")
        return branch

    def short_commit(self) -> str:
        try:
            return self._run(["rev-parse", "--short", "HEAD"])
        except GitError:
            return ""

    def config_value(self, key: str) -> str:
        try:
            return self._run(["config", "--get", key])
        except GitError:
            return ""

    def changes(self) -> list[GitChange]:
        """Return working-tree + staged changes via ``git status --porcelain``."""
        raw = self._run(["status", "--porcelain"])
        changes: list[GitChange] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            status, path = line[:2], line[3:].strip()
            # handle rename "old -> new" by taking the destination
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            changes.append(GitChange(status=status, path=path.replace("\\", "/")))
        return changes

    def latest_tag(self) -> str:
        try:
            return self._run(["describe", "--tags", "--abbrev=0"])
        except GitError:
            return ""

    def list_tags(self) -> list[str]:
        out = self._run(["tag", "--list"])
        return [t for t in out.splitlines() if t.strip()]

    def log_since(self, ref: str = "", limit: int = 50) -> list[str]:
        """Return ``subject`` lines since ``ref`` (or last ``limit`` commits)."""
        args = ["log", f"--max-count={limit}", "--pretty=%h\t%s"]
        if ref:
            args.insert(1, f"{ref}..HEAD")
        try:
            out = self._run(args)
        except GitError:
            return []
        return [line for line in out.splitlines() if line.strip()]

    # ---- mutations -------------------------------------------------------
    def add(self, paths: list[str]) -> None:
        if not paths:
            return
        self._run(["add", "--", *paths], mutating=True)

    def commit(self, message: str) -> None:
        self._run(["commit", "-m", message], mutating=True)

    def push(self, remote: str, branch: str) -> None:
        self._run(["push", remote, branch], mutating=True)

    def pull(self, remote: str, branch: str) -> None:
        self._run(["pull", remote, branch], mutating=True)

    def tag(self, name: str, message: str = "") -> None:
        if message:
            self._run(["tag", "-a", name, "-m", message], mutating=True)
        else:
            self._run(["tag", name], mutating=True)

    def push_tag(self, remote: str, name: str) -> None:
        self._run(["push", remote, name], mutating=True)
