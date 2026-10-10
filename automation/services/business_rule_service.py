"""Business Rule Registry — permanent identifiers, JSON source of truth, generated docs.

Each rule receives a permanent identifier ``<PREFIX>-BR-NNN`` where the prefix is
resolved per module from configuration (e.g. Procurement → ``PR-BR``). Identifiers
are never reused. The JSON registry is authoritative; Markdown is generated from it.
"""

from __future__ import annotations

import json
import re

from ..builders.rule_builder import RuleBuilder
from ..core.config import NDFConfig
from ..core.exceptions import RegistryError
from ..core.logging import get_logger
from ..core.models import BusinessRule
from ..core.paths import PathResolver
from ..core.result import CommandResult

_REGISTRY_FILE = "registry.json"


class BusinessRuleService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, clock) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._clock = clock
        self._prefixes = config.rule_prefixes
        self._default_prefix = config.get("rules", "default_prefix", "GEN-BR")
        self._builder = RuleBuilder()
        self._log = get_logger("services.rules")

    # ---- registry I/O ----------------------------------------------------
    def _registry_path(self):
        return self._paths.business_rules_dir / _REGISTRY_FILE

    def load_rules(self) -> list[BusinessRule]:
        path = self._registry_path()
        if not self._fs.exists(path):
            return []
        data = json.loads(self._fs.read_text(path, default="[]"))
        return [
            BusinessRule(
                identifier=item["identifier"],
                name=item["name"],
                module=item["module"],
                version=item.get("version", "1.0"),
                priority=item.get("priority", "Medium"),
                status=item.get("status", "Active"),
                dependencies=list(item.get("dependencies", [])),
                description=item.get("description", ""),
                path=item.get("path", ""),
            )
            for item in data
        ]

    def _save_registry(self, rules: list[BusinessRule]) -> str:
        payload = [
            {
                "identifier": r.identifier,
                "name": r.name,
                "module": r.module,
                "version": r.version,
                "priority": r.priority,
                "status": r.status,
                "dependencies": r.dependencies,
                "description": r.description,
                "path": r.path,
            }
            for r in rules
        ]
        return self._fs.write_text(
            self._registry_path(), json.dumps(payload, indent=2, ensure_ascii=False)
        )

    # ---- operations ------------------------------------------------------
    def _prefix_for(self, module: str) -> str:
        return self._prefixes.get(module, self._default_prefix)

    def _next_identifier(self, prefix: str, rules: list[BusinessRule]) -> str:
        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        numbers = [int(m.group(1)) for r in rules if (m := pattern.match(r.identifier))]
        return f"{prefix}-{(max(numbers, default=0) + 1):03d}"

    def register(
        self,
        name: str,
        module: str,
        priority: str = "Medium",
        status: str = "Active",
        version: str = "1.0",
        dependencies: list[str] | None = None,
        description: str = "",
    ) -> CommandResult:
        if not name.strip():
            raise RegistryError("Business rule name must not be empty")
        if not module.strip():
            raise RegistryError("Business rule module must not be empty")

        rules = self.load_rules()
        prefix = self._prefix_for(module)
        identifier = self._next_identifier(prefix, rules)
        rule = BusinessRule(
            identifier=identifier,
            name=name.strip(),
            module=module.strip(),
            version=version,
            priority=priority,
            status=status,
            dependencies=dependencies or [],
            description=description.strip(),
        )
        doc_path = self._paths.business_rules_dir / f"{identifier}-{self._slug(name)}.md"
        rule.path = self._paths.relative(doc_path)

        rules.append(rule)
        changed = [
            self._fs.write_text(doc_path, self._builder.render_rule(rule)),
            self._save_registry(rules),
            self.rebuild_index(),
        ]
        self._log.info("registered business rule %s (%s)", identifier, module)
        return CommandResult.success(
            f"Registered {identifier}: {rule.name} [{module}].",
            changed_paths=changed,
            data={"identifier": identifier},
        )

    def rebuild_index(self) -> str:
        rules = self.load_rules()
        content = self._builder.render_index(rules, self._clock.today_iso())
        return self._fs.write_text(self._paths.business_rules_dir / "INDEX.md", content)

    def list_rules(self) -> list[BusinessRule]:
        return self.load_rules()

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "rule"
