"""Configuration loading for NDF.

Configuration is layered (highest precedence first):
  1. Path in the ``NDF_CONFIG`` environment variable.
  2. ``ndf.config.toml`` at the repository root (project override).
  3. The bundled default ``automation/config/ndf.config.toml``.

All values are merged over the bundled defaults so a partial override file is valid.
No absolute paths are stored; every path under ``[paths]`` is repository-relative.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .exceptions import ConfigError

_BUNDLED_DEFAULT = Path(__file__).resolve().parent.parent / "config" / "ndf.config.toml"
_PROJECT_OVERRIDE_NAME = "ndf.config.toml"
_ENV_VAR = "NDF_CONFIG"


@dataclass(slots=True)
class NDFConfig:
    """Parsed, merged framework configuration.

    The raw merged mapping is retained in :attr:`raw`; typed accessors expose the
    groups that services depend on.
    """

    raw: dict[str, Any] = field(default_factory=dict)
    source_paths: list[str] = field(default_factory=list)

    # ---- typed accessors -------------------------------------------------
    @property
    def project(self) -> dict[str, Any]:
        return self.raw.get("project", {})

    @property
    def paths(self) -> dict[str, str]:
        return self.raw.get("paths", {})

    @property
    def version(self) -> dict[str, Any]:
        return self.raw.get("version", {})

    @property
    def changelog_sections(self) -> list[str]:
        return list(self.raw.get("changelog", {}).get("sections", []))

    @property
    def commit(self) -> dict[str, Any]:
        return self.raw.get("commit", {})

    @property
    def github(self) -> dict[str, Any]:
        return self.raw.get("github", {})

    @property
    def health(self) -> dict[str, Any]:
        return self.raw.get("health", {})

    @property
    def modules(self) -> list[dict[str, Any]]:
        return list(self.raw.get("modules", {}).get("items", []))

    @property
    def rule_prefixes(self) -> dict[str, str]:
        return dict(self.raw.get("rules", {}).get("prefixes", {}))

    @property
    def ai(self) -> dict[str, Any]:
        return self.raw.get("ai", {})

    def get(self, group: str, key: str, default: Any = None) -> Any:
        return self.raw.get(group, {}).get(key, default)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"Configuration file not found: {path}")
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto a copy of ``base``."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(repo_root: Path) -> NDFConfig:
    """Load and merge configuration for the given repository root."""
    if not _BUNDLED_DEFAULT.exists():
        raise ConfigError(f"Bundled default configuration is missing: {_BUNDLED_DEFAULT}")

    merged = _read_toml(_BUNDLED_DEFAULT)
    sources = [str(_BUNDLED_DEFAULT)]

    project_override = repo_root / _PROJECT_OVERRIDE_NAME
    if project_override.exists():
        merged = _deep_merge(merged, _read_toml(project_override))
        sources.append(str(project_override))

    env_override = os.environ.get(_ENV_VAR)
    if env_override:
        env_path = Path(env_override).expanduser()
        merged = _deep_merge(merged, _read_toml(env_path))
        sources.append(str(env_path))

    return NDFConfig(raw=merged, source_paths=sources)
