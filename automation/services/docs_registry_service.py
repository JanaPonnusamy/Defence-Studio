"""Documentation registry — scans the docs tree and builds docs/INDEX.md.

A document's metadata comes from an optional simple frontmatter block at the top of
the file:

    ---
    module: Procurement
    version: 1.0
    status: Approved
    owner: Solution Architecture
    updated: 2026-06-27
    ---

Missing fields fall back to conventions: module from the first path segment under
docs/, document title from the first H1 heading (or the filename).
"""

from __future__ import annotations

import re

from ..builders.index_builder import IndexBuilder
from ..core.config import NDFConfig
from ..core.logging import get_logger
from ..core.models import DocRecord
from ..core.paths import PathResolver
from ..core.result import CommandResult

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_KV = re.compile(r"^(\w+)\s*:\s*(.+)$")


class DocsRegistryService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, clock,
                 adr_service, business_rule_service) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._clock = clock
        self._adr = adr_service
        self._rules = business_rule_service
        self._builder = IndexBuilder()
        self._excluded_names = set(config.get("docs", "exclude_files", ["INDEX.md"]))
        self._log = get_logger("services.docs")

    def scan(self) -> list[DocRecord]:
        records: list[DocRecord] = []
        docs_dir = self._paths.docs_dir
        for path in self._fs.rglob(docs_dir, "*.md"):
            if path.name in self._excluded_names:
                continue
            # ADRs and rule docs are surfaced in their own sections.
            if self._paths.adr_dir in path.parents or self._paths.business_rules_dir in path.parents:
                continue
            text = self._fs.read_text(path, default="")
            meta = self._frontmatter(text)
            rel = self._paths.relative(path)
            records.append(
                DocRecord(
                    module=meta.get("module") or self._infer_module(path, docs_dir),
                    document=meta.get("title") or self._title(text, path.stem),
                    path=rel,
                    version=meta.get("version", ""),
                    status=meta.get("status", "Draft"),
                    owner=meta.get("owner", ""),
                    updated_date=meta.get("updated", ""),
                )
            )
        return sorted(records, key=lambda r: (r.module.lower(), r.document.lower()))

    def build(self) -> CommandResult:
        docs = self.scan()
        # keep ADR/rule indexes fresh, then compose the master index
        self._adr.rebuild_index()
        self._rules.rebuild_index()
        adrs = self._adr.list_records()
        rules = self._rules.list_rules()
        content = self._builder.render(docs, adrs, rules, self._clock.today_iso())
        rel = self._fs.write_text(self._paths.index_file, content)
        self._log.info("docs index built: %d docs, %d ADRs, %d rules",
                      len(docs), len(adrs), len(rules))
        return CommandResult.success(
            f"Documentation index built: {len(docs)} document(s), "
            f"{len(adrs)} ADR(s), {len(rules)} rule(s).",
            changed_paths=[rel],
            data={"docs": len(docs), "adrs": len(adrs), "rules": len(rules)},
        )

    # ---- helpers ---------------------------------------------------------
    def _frontmatter(self, text: str) -> dict[str, str]:
        match = _FRONTMATTER.match(text)
        if not match:
            return {}
        meta: dict[str, str] = {}
        for line in match.group(1).splitlines():
            kv = _KV.match(line.strip())
            if kv:
                meta[kv.group(1).lower()] = kv.group(2).strip().strip('"')
        return meta

    def _title(self, text: str, fallback: str) -> str:
        match = _H1.search(text)
        return match.group(1).strip() if match else fallback

    def _infer_module(self, path, docs_dir) -> str:
        rel = path.resolve().relative_to(docs_dir.resolve())
        parts = rel.parts
        if len(parts) > 1:
            return parts[0]
        return "Platform"
