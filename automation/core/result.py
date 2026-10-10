"""The :class:`CommandResult` value object returned by every command/service action."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CommandResult:
    """Outcome of a command or service action.

    Attributes:
        ok: Whether the action succeeded.
        message: Human-readable summary, suitable for CLI output.
        changed_paths: Repository-relative paths that were created or modified.
        data: Optional structured payload for callers/tests.
    """

    ok: bool
    message: str
    changed_paths: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        message: str,
        changed_paths: list[str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> "CommandResult":
        return cls(True, message, list(changed_paths or []), dict(data or {}))

    @classmethod
    def failure(cls, message: str, data: dict[str, Any] | None = None) -> "CommandResult":
        return cls(False, message, [], dict(data or {}))

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1
