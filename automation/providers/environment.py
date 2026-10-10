"""Environment provider — developer identity and environment variables.

Used to populate the ``Developer`` field of ``VERSION.md`` and similar provenance.
Resolution order for the developer name: ``NDF_DEVELOPER`` env var, then git
``user.name``, then the OS user, then ``"unknown"``.
"""

from __future__ import annotations

import getpass
import os

from .git_provider import GitProvider


class EnvironmentProvider:
    """Resolves developer identity and exposes environment variables."""

    def __init__(self, git: GitProvider) -> None:
        self._git = git

    def developer(self) -> str:
        explicit = os.environ.get("NDF_DEVELOPER")
        if explicit:
            return explicit
        git_name = self._git.config_value("user.name")
        if git_name:
            return git_name
        try:
            return getpass.getuser()
        except Exception:  # pragma: no cover - extremely rare
            return "unknown"

    def get(self, key: str, default: str = "") -> str:
        return os.environ.get(key, default)
