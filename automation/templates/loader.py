"""Loads and renders ``{{placeholder}}`` text templates bundled with NDF."""

from __future__ import annotations

import re
from pathlib import Path

from ..core.exceptions import ConfigError

_TEMPLATE_DIR = Path(__file__).resolve().parent
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load_template(name: str) -> str:
    path = _TEMPLATE_DIR / name
    if not path.exists():
        raise ConfigError(f"Template not found: {name}")
    return path.read_text(encoding="utf-8")


def render_template(name: str, values: dict[str, str]) -> str:
    """Render a template, substituting ``{{key}}`` markers from ``values``.

    Unknown placeholders are left untouched so partial templates stay visible.
    """
    template = load_template(name)

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(values.get(key, match.group(0)))

    return _PLACEHOLDER.sub(_sub, template)
