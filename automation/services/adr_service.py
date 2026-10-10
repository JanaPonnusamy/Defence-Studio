"""Architecture Decision Records — automatic numbering, creation, and indexing."""

from __future__ import annotations

import re

from ..builders.adr_builder import AdrBuilder
from ..core.config import NDFConfig
from ..core.exceptions import RegistryError
from ..core.logging import get_logger
from ..core.models import AdrRecord
from ..core.paths import PathResolver
from ..core.result import CommandResult

_ADR_FILE = re.compile(r"^ADR-(\d+)-(.+)\.md$", re.IGNORECASE)
_TITLE_LINE = re.compile(r"^#\s*ADR-\d+:\s*(.+)$", re.MULTILINE)
_STATUS_LINE = re.compile(r"^\|\s*Status\s*\|\s*(.+?)\s*\|", re.MULTILINE)
_DATE_LINE = re.compile(r"^\|\s*Date\s*\|\s*(.+?)\s*\|", re.MULTILINE)


class AdrService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, clock) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._clock = clock
        self._builder = AdrBuilder()
        self._log = get_logger("services.adr")

    def list_records(self) -> list[AdrRecord]:
        records: list[AdrRecord] = []
        for path in self._fs.glob(self._paths.adr_dir, "ADR-*.md"):
            match = _ADR_FILE.match(path.name)
            if not match:
                continue
            text = self._fs.read_text(path, default="")
            title_match = _TITLE_LINE.search(text)
            title = title_match.group(1).strip() if title_match else match.group(2).replace("-", " ")
            status_match = _STATUS_LINE.search(text)
            date_match = _DATE_LINE.search(text)
            records.append(
                AdrRecord(
                    number=int(match.group(1)),
                    title=title,
                    status=status_match.group(1).strip() if status_match else "Proposed",
                    date=date_match.group(1).strip() if date_match else "",
                    path=self._paths.relative(path),
                )
            )
        return sorted(records, key=lambda r: r.number)

    def next_number(self) -> int:
        records = self.list_records()
        return (max((r.number for r in records), default=0)) + 1

    def create(self, title: str, status: str = "Proposed", deciders: str = "") -> CommandResult:
        if not title.strip():
            raise RegistryError("ADR title must not be empty")
        number = self.next_number()
        slug = self._slug(title)
        record = AdrRecord(
            number=number,
            title=title.strip(),
            status=status,
            date=self._clock.today_iso(),
        )
        path = self._paths.adr_dir / f"{record.identifier}-{slug}.md"
        record.path = self._paths.relative(path)
        rel_doc = self._fs.write_text(path, self._builder.render_adr(record, deciders))
        rel_index = self.rebuild_index()
        self._log.info("created %s", record.identifier)
        return CommandResult.success(
            f"Created {record.identifier}: {record.title}.",
            changed_paths=[rel_doc, rel_index],
            data={"identifier": record.identifier},
        )

    def rebuild_index(self) -> str:
        records = self.list_records()
        content = self._builder.render_index(records, self._clock.today_iso())
        return self._fs.write_text(self._paths.adr_dir / "INDEX.md", content)

    @staticmethod
    def _slug(title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return slug or "decision"
