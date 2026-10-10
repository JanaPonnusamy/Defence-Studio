"""AI Documentation Archive — provenance for ChatGPT/Claude generated documentation.

Maintains, under the AI archive directory:
  - a JSON ledger (source of truth) of artifacts,
  - prompt-history / approved / rejected sub-folders for the artifact bodies,
  - an INDEX.md with AI Source, Approval Status, and Revision History.
"""

from __future__ import annotations

import json
import re

from ..builders.markdown import MarkdownBuilder
from ..core.config import NDFConfig
from ..core.exceptions import RegistryError
from ..core.logging import get_logger
from ..core.models import AiArtifact
from ..core.paths import PathResolver
from ..core.result import CommandResult

_LEDGER = "ledger.json"
_SOURCES = ("ChatGPT", "Claude")
_STATES = ("Pending", "Approved", "Rejected")
_FOLDER = {"Pending": "prompt-history", "Approved": "approved", "Rejected": "rejected"}


class AiArchiveService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem, clock) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._clock = clock
        self._sources = tuple(config.ai.get("sources", _SOURCES))
        self._log = get_logger("services.ai")

    def _ledger_path(self):
        return self._paths.ai_archive_dir / _LEDGER

    def load(self) -> list[AiArtifact]:
        path = self._ledger_path()
        if not self._fs.exists(path):
            return []
        data = json.loads(self._fs.read_text(path, default="[]"))
        return [
            AiArtifact(
                identifier=i["identifier"],
                source=i["source"],
                kind=i.get("kind", "output"),
                title=i.get("title", ""),
                approval_status=i.get("approval_status", "Pending"),
                revision=int(i.get("revision", 1)),
                date=i.get("date", ""),
                path=i.get("path", ""),
            )
            for i in data
        ]

    def _save(self, artifacts: list[AiArtifact]) -> str:
        payload = [
            {
                "identifier": a.identifier,
                "source": a.source,
                "kind": a.kind,
                "title": a.title,
                "approval_status": a.approval_status,
                "revision": a.revision,
                "date": a.date,
                "path": a.path,
            }
            for a in artifacts
        ]
        return self._fs.write_text(self._ledger_path(), json.dumps(payload, indent=2, ensure_ascii=False))

    def add(self, source: str, title: str, kind: str = "output", body: str = "") -> CommandResult:
        if source not in self._sources:
            raise RegistryError(f"Unknown AI source '{source}'. Allowed: {', '.join(self._sources)}")
        artifacts = self.load()
        identifier = f"AI-{len(artifacts) + 1:04d}"
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "artifact"
        doc_path = self._paths.ai_archive_dir / _FOLDER["Pending"] / f"{identifier}-{slug}.md"
        artifact = AiArtifact(
            identifier=identifier,
            source=source,
            kind=kind,
            title=title.strip(),
            approval_status="Pending",
            revision=1,
            date=self._clock.today_iso(),
            path=self._paths.relative(doc_path),
        )
        artifacts.append(artifact)
        header = (
            f"# {identifier}: {title}\n\n"
            f"- **Source:** {source}\n- **Kind:** {kind}\n"
            f"- **Status:** Pending\n- **Date:** {artifact.date}\n\n---\n\n"
        )
        changed = [
            self._fs.write_text(doc_path, header + (body or "_Body to be attached._")),
            self._save(artifacts),
            self.rebuild_index(),
        ]
        self._log.info("archived AI artifact %s (%s)", identifier, source)
        return CommandResult.success(
            f"Archived {identifier} from {source}.",
            changed_paths=changed,
            data={"identifier": identifier},
        )

    def set_status(self, identifier: str, status: str) -> CommandResult:
        if status not in _STATES:
            raise RegistryError(f"Unknown status '{status}'. Allowed: {', '.join(_STATES)}")
        artifacts = self.load()
        target = next((a for a in artifacts if a.identifier == identifier), None)
        if target is None:
            raise RegistryError(f"AI artifact '{identifier}' not found")
        if target.approval_status != status:
            target.revision += 1
        target.approval_status = status
        changed = [self._save(artifacts), self.rebuild_index()]
        self._log.info("AI artifact %s -> %s (rev %d)", identifier, status, target.revision)
        return CommandResult.success(
            f"{identifier} marked {status} (revision {target.revision}).",
            changed_paths=changed,
        )

    def rebuild_index(self) -> str:
        artifacts = self.load()
        md = MarkdownBuilder()
        md.heading("AI Documentation Archive", 1)
        md.paragraph(
            "Provenance for AI-generated documentation. "
            f"Last updated: {self._clock.today_iso()}."
        )
        if artifacts:
            md.table(
                ["ID", "Title", "AI Source", "Kind", "Approval Status", "Revision", "Date"],
                [
                    [a.identifier, f"[{a.title}]({a.path})" if a.path else a.title,
                     a.source, a.kind, a.approval_status, str(a.revision), a.date]
                    for a in artifacts
                ],
            )
        else:
            md.paragraph("_No AI artifacts archived yet._")
        return self._fs.write_text(self._paths.ai_archive_dir / "INDEX.md", md.render())

    def list_artifacts(self) -> list[AiArtifact]:
        return self.load()
