"""Project dashboard generation (PROJECT_STATUS.md)."""

from __future__ import annotations

from ..builders.dashboard_builder import DashboardBuilder
from ..core.config import NDFConfig
from ..core.logging import get_logger
from ..core.models import ModuleStatus
from ..core.paths import PathResolver
from ..core.result import CommandResult


class DashboardService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, git, clock,
                 version_service) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._git = git
        self._clock = clock
        self._versions = version_service
        self._builder = DashboardBuilder()
        self._recent_limit = int(config.get("dashboard", "recent_commits", 10))
        self._log = get_logger("services.dashboard")

    def modules(self) -> list[ModuleStatus]:
        return [
            ModuleStatus(
                name=item.get("name", "Unnamed"),
                status=item.get("status", "Planned"),
                completion_pct=int(item.get("completion", 0)),
                owner=item.get("owner", ""),
                milestone=item.get("milestone", ""),
            )
            for item in self._config.modules
        ]

    def recent_work(self) -> list[str]:
        items: list[str] = []
        for raw in self._git.log_since(limit=self._recent_limit):
            commit_hash, _, subject = raw.partition("\t")
            items.append(f"`{commit_hash.strip()}` {subject.strip()}")
        return items

    def build(self) -> CommandResult:
        version = self._versions.current()
        modules = self.modules()
        recent = self.recent_work()
        content = self._builder.render(version, modules, recent, self._clock.today_iso())
        rel = self._fs.write_text(self._paths.status_file, content)
        self._log.info("dashboard built (%d modules)", len(modules))
        return CommandResult.success(
            f"Project dashboard built ({len(modules)} module(s) tracked).",
            changed_paths=[rel],
            data={"modules": len(modules)},
        )
