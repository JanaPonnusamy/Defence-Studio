"""Composition root / lightweight dependency-injection container.

The container is the single place that wires configuration, the path resolver, the
providers, the builders and the services together. Commands ask the container for a
service; they never construct providers or reach for globals. Services are created
lazily and memoized.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from .config import NDFConfig, load_config
from .logging import configure_logging, get_logger
from .paths import PathResolver, detect_repo_root


class Container:
    """Application context: holds config + path resolver and builds services on demand."""

    def __init__(self, repo_root: Path | None = None, log_level: str | None = None) -> None:
        self.repo_root = (repo_root or detect_repo_root()).resolve()
        self.config: NDFConfig = load_config(self.repo_root)
        configure_logging(log_level or str(self.config.get("logging", "level", "INFO")))
        self.log = get_logger("container")
        self.paths = PathResolver(self.repo_root, self.config)

    # ---- providers -------------------------------------------------------
    @cached_property
    def filesystem(self):
        from ..providers.filesystem import FileSystemProvider

        return FileSystemProvider(self.repo_root)

    @cached_property
    def clock(self):
        from ..providers.clock import ClockProvider

        return ClockProvider()

    @cached_property
    def git(self):
        from ..providers.git_provider import GitProvider

        return GitProvider(self.repo_root)

    @cached_property
    def environment(self):
        from ..providers.environment import EnvironmentProvider

        return EnvironmentProvider(self.git)

    # ---- services --------------------------------------------------------
    @cached_property
    def version_service(self):
        from ..services.version_service import VersionService

        return VersionService(self.config, self.paths, self.filesystem, self.git,
                              self.clock, self.environment)

    @cached_property
    def changelog_service(self):
        from ..services.changelog_service import ChangelogService

        return ChangelogService(self.config, self.paths, self.filesystem, self.git, self.clock)

    @cached_property
    def release_service(self):
        from ..services.release_service import ReleaseService

        return ReleaseService(self.config, self.paths, self.filesystem, self.git,
                             self.clock, self.version_service, self.changelog_service)

    @cached_property
    def github_sync_service(self):
        from ..services.github_sync_service import GitHubSyncService

        return GitHubSyncService(self.config, self.paths, self.git, self.version_service)

    @cached_property
    def docs_registry_service(self):
        from ..services.docs_registry_service import DocsRegistryService

        return DocsRegistryService(self.config, self.paths, self.filesystem, self.clock,
                                  self.adr_service, self.business_rule_service)

    @cached_property
    def adr_service(self):
        from ..services.adr_service import AdrService

        return AdrService(self.config, self.paths, self.filesystem, self.clock)

    @cached_property
    def business_rule_service(self):
        from ..services.business_rule_service import BusinessRuleService

        return BusinessRuleService(self.config, self.paths, self.filesystem, self.clock)

    @cached_property
    def dashboard_service(self):
        from ..services.dashboard_service import DashboardService

        return DashboardService(self.config, self.paths, self.filesystem, self.git,
                               self.clock, self.version_service)

    @cached_property
    def ai_archive_service(self):
        from ..services.ai_archive_service import AiArchiveService

        return AiArchiveService(self.config, self.paths, self.filesystem, self.clock)

    @cached_property
    def repo_health_service(self):
        from ..services.repo_health_service import RepoHealthService

        return RepoHealthService(self.config, self.paths, self.filesystem)
