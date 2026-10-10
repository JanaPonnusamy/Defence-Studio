"""Version management — the single source of truth is the toml block in VERSION.md."""

from __future__ import annotations

import re
import tomllib

from ..builders.version_builder import VersionBuilder
from ..core.config import NDFConfig
from ..core.exceptions import VersionError
from ..core.logging import get_logger
from ..core.models import VersionInfo
from ..core.paths import PathResolver
from ..core.result import CommandResult

_TOML_BLOCK = re.compile(r"```toml\s*(.*?)```", re.DOTALL)
_BUMP_LEVELS = ("major", "minor", "patch", "build")


class VersionService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, git,
                 clock, environment) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._git = git
        self._clock = clock
        self._env = environment
        self._builder = VersionBuilder()
        self._log = get_logger("services.version")

    # ---- read ------------------------------------------------------------
    def current(self) -> VersionInfo:
        """Read the current VersionInfo, or a configured initial version if absent."""
        path = self._paths.version_file
        if not self._fs.exists(path):
            return self._initial()
        text = self._fs.read_text(path)
        match = _TOML_BLOCK.search(text)
        if not match:
            raise VersionError(f"{self._paths.relative(path)} has no machine-readable toml block")
        data = tomllib.loads(match.group(1)).get("version", {})
        return VersionInfo(
            major=int(data.get("major", 0)),
            minor=int(data.get("minor", 0)),
            patch=int(data.get("patch", 0)),
            build_number=int(data.get("build_number", 0)),
            build_date=str(data.get("build_date", "")),
            release_name=str(data.get("release_name", "")),
            developer=str(data.get("developer", "")),
            git_commit=str(data.get("git_commit", "")),
            branch=str(data.get("branch", "")),
        )

    def _initial(self) -> VersionInfo:
        cfg = self._config.version
        return VersionInfo(
            major=int(cfg.get("initial_major", 0)),
            minor=int(cfg.get("initial_minor", 1)),
            patch=int(cfg.get("initial_patch", 0)),
            build_number=0,
            release_name=str(cfg.get("release_name", self._config.project.get("name", "NEXORA"))),
        )

    # ---- write -----------------------------------------------------------
    def bump(self, level: str) -> CommandResult:
        if level not in _BUMP_LEVELS:
            raise VersionError(f"Unknown bump level '{level}'. Use one of: {', '.join(_BUMP_LEVELS)}")
        version = self.current()
        if level == "major":
            version.major += 1
            version.minor = 0
            version.patch = 0
        elif level == "minor":
            version.minor += 1
            version.patch = 0
        elif level == "patch":
            version.patch += 1
        # every bump increments the build number
        version.build_number += 1
        self._stamp(version)
        rel = self._write(version)
        self._log.info("version bumped (%s) -> %s", level, version.full)
        return CommandResult.success(
            f"Version bumped ({level}) to {version.semver} (build {version.build_number}).",
            changed_paths=[rel],
            data={"version": version.full},
        )

    def set_release_name(self, name: str) -> CommandResult:
        version = self.current()
        version.release_name = name
        self._stamp(version)
        rel = self._write(version)
        return CommandResult.success(f"Release name set to '{name}'.", changed_paths=[rel])

    def ensure_initialized(self) -> CommandResult:
        """Create VERSION.md from the initial version if it does not exist."""
        if self._fs.exists(self._paths.version_file):
            return CommandResult.success("VERSION.md already present.")
        version = self._initial()
        self._stamp(version)
        rel = self._write(version)
        return CommandResult.success("Initialized VERSION.md.", changed_paths=[rel])

    def _stamp(self, version: VersionInfo) -> None:
        version.build_date = self._clock.today_iso()
        version.developer = self._env.developer()
        version.git_commit = self._git.short_commit()
        try:
            version.branch = self._git.current_branch()
        except Exception:  # detached HEAD or non-repo — leave blank
            version.branch = version.branch or ""
        if not version.release_name:
            version.release_name = str(
                self._config.version.get("release_name", self._config.project.get("name", "NEXORA"))
            )

    def _write(self, version: VersionInfo) -> str:
        return self._fs.write_text(self._paths.version_file, self._builder.render(version))
