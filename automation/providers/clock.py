"""Time provider — the single source of "now" for the framework."""

from __future__ import annotations

from datetime import datetime, timezone


class ClockProvider:
    """Supplies the current date/time so generated artifacts are deterministic in tests."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today_iso(self) -> str:
        """ISO date string, e.g. ``2026-06-27``."""
        return self.now().date().isoformat()

    def timestamp_iso(self) -> str:
        """ISO-8601 timestamp to the second."""
        return self.now().replace(microsecond=0).isoformat()
