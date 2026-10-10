"""Repository health reporting.

Produces findings across five categories — large files, duplicate documents,
broken links, missing documentation, and orphan files — and renders a report.
"""

from __future__ import annotations

import hashlib
import re

from ..builders.markdown import MarkdownBuilder
from ..core.config import NDFConfig
from ..core.logging import get_logger
from ..core.models import HealthFinding
from ..core.paths import PathResolver
from ..core.result import CommandResult

_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)


class RepoHealthService:
    def __init__(self, config: NDFConfig, paths: PathResolver, filesystem) -> None:
        self._config = config
        self._paths = paths
        self._fs = filesystem
        self._health = config.health
        self._excludes = set(self._health.get("exclude_dirs", [
            ".git", "node_modules", "venv", ".venv", "backup", "dist", "__pycache__",
        ]))
        self._large_bytes = int(self._health.get("large_file_mb", 5)) * 1024 * 1024
        self._doc_exts = set(self._health.get("doc_extensions", [".md"]))
        self._log = get_logger("services.health")

    # ---- scanning helpers ------------------------------------------------
    def _all_files(self) -> list:
        root = self._paths.root
        result = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in self._excludes for part in path.relative_to(root).parts):
                continue
            result.append(path)
        return result

    def _doc_files(self, files) -> list:
        return [p for p in files if p.suffix.lower() in self._doc_exts]

    # ---- individual checks ----------------------------------------------
    def large_files(self, files) -> list[HealthFinding]:
        findings = []
        for path in files:
            size = self._fs.size_bytes(path)
            if size > self._large_bytes:
                findings.append(HealthFinding(
                    "large", self._paths.relative(path),
                    f"{size / (1024 * 1024):.1f} MB", "warning"))
        return findings

    def duplicate_documents(self, docs) -> list[HealthFinding]:
        by_hash: dict[str, list[str]] = {}
        for path in docs:
            content = self._fs.read_text(path, default="")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            by_hash.setdefault(digest, []).append(self._paths.relative(path))
        findings = []
        for paths in by_hash.values():
            if len(paths) > 1:
                findings.append(HealthFinding(
                    "duplicate", paths[0],
                    "identical to: " + ", ".join(paths[1:]), "warning"))
        return findings

    def broken_links(self, docs) -> list[HealthFinding]:
        findings = []
        for path in docs:
            text = self._fs.read_text(path, default="")
            for target in _MD_LINK.findall(text):
                link = target.split("#", 1)[0].strip()
                if not link or link.startswith(("http://", "https://", "mailto:")):
                    continue
                candidate = (path.parent / link).resolve()
                root_candidate = (self._paths.root / link.lstrip("/")).resolve()
                if not candidate.exists() and not root_candidate.exists():
                    findings.append(HealthFinding(
                        "broken-link", self._paths.relative(path),
                        f"missing target: {link}", "error"))
        return findings

    def missing_documentation(self, docs) -> list[HealthFinding]:
        haystack = []
        for path in docs:
            text = self._fs.read_text(path, default="")
            title = _H1.search(text)
            haystack.append((path.name.lower(), (title.group(1).lower() if title else "")))
        findings = []
        for item in self._config.modules:
            name = item.get("name", "")
            if not name:
                continue
            token = name.lower()
            documented = any(token in fn or token in heading for fn, heading in haystack)
            if not documented:
                findings.append(HealthFinding(
                    "missing-doc", name, "no documentation found for module", "warning"))
        return findings

    def orphan_files(self, docs) -> list[HealthFinding]:
        referenced: set[str] = set()
        for path in docs:
            text = self._fs.read_text(path, default="")
            for target in _MD_LINK.findall(text):
                link = target.split("#", 1)[0].strip().lstrip("/")
                if link and not link.startswith(("http://", "https://", "mailto:")):
                    referenced.add(link.replace("\\", "/").split("/")[-1])
        findings = []
        keep = {"INDEX.md", "README.md", "CHANGELOG.md", "VERSION.md",
                "RELEASE_NOTES.md", "PROJECT_STATUS.md"}
        for path in docs:
            if path.name in keep:
                continue
            if path.name not in referenced:
                findings.append(HealthFinding(
                    "orphan", self._paths.relative(path),
                    "not linked from any document", "info"))
        return findings

    # ---- report ----------------------------------------------------------
    def report(self) -> CommandResult:
        files = self._all_files()
        docs = self._doc_files(files)
        sections = {
            "Large Files": self.large_files(files),
            "Duplicate Documents": self.duplicate_documents(docs),
            "Broken Links": self.broken_links(docs),
            "Missing Documentation": self.missing_documentation(docs),
            "Orphan Files": self.orphan_files(docs),
        }
        total = sum(len(v) for v in sections.values())

        md = MarkdownBuilder()
        md.heading("NEXORA Platform — Repository Health Report", 1)
        md.paragraph(
            f"Generated by the NEXORA Development Framework. "
            f"{total} finding(s) across {len(sections)} checks."
        )
        for title, findings in sections.items():
            md.heading(title, 2)
            if findings:
                md.table(
                    ["Target", "Detail", "Severity"],
                    [[f.target, f.detail, f.severity] for f in findings],
                )
            else:
                md.paragraph("_No issues found._")

        rel = self._fs.write_text(self._paths.reports_dir / "repo_health.md", md.render())
        self._log.info("repo health report generated (%d findings)", total)
        return CommandResult.success(
            f"Repository health report generated: {total} finding(s).",
            changed_paths=[rel],
            data={"findings": total},
        )
