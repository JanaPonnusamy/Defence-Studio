"""Exception hierarchy for the NEXORA Development Framework.

All framework errors derive from :class:`NDFError` so command handlers can catch a
single base type, log it, and exit cleanly without leaking stack traces to users.
"""

from __future__ import annotations


class NDFError(Exception):
    """Base class for every NDF error."""


class ConfigError(NDFError):
    """Raised when configuration is missing, malformed, or invalid."""


class PathError(NDFError):
    """Raised when a required path cannot be resolved within the repository."""


class GitError(NDFError):
    """Raised when a git operation fails or the repository state is invalid."""


class RegistryError(NDFError):
    """Raised on invalid registry operations (ADR, business rules, docs index)."""


class VersionError(NDFError):
    """Raised on invalid version state or bump requests."""


class CommandError(NDFError):
    """Raised for invalid command invocation (bad arguments, unknown action)."""
