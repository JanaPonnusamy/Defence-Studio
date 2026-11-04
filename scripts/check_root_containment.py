"""Fail when Defence Era source points project data outside the repository.

Scans product source for uncontrolled temporary directories, hardcoded
project paths, and symlinks or junctions whose targets leave the root.
Historical evidence under ``AGENT_WORK`` and third-party trees
(virtualenvs, ``site-packages``, ``node_modules``) are not product source.

Run from anywhere:

    python scripts/check_root_containment.py
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "backend" / "config" / "root_containment_allowlist.txt"
SCAN_SUFFIXES = {".py", ".ps1", ".js", ".mjs"}
SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "candidate-env",
    "node_modules",
    "site-packages",
    "tests",
    "venv",
}
SKIP_PREFIXES = (".venv",)
SKIP_TREES = {"AGENT_WORK", "third_party", "analysis", "data"}
SELF_PATHS = {
    "scripts/check_root_containment.py",
    "scripts/inventory_external_resources.py",
}
TEMP_CALLS = {"TemporaryDirectory", "mkdtemp", "mkstemp", "NamedTemporaryFile", "gettempdir"}
TEMP_OK_WITH_DIR = {"TemporaryDirectory", "mkdtemp", "mkstemp", "NamedTemporaryFile"}
# A Windows path. URL schemes (http://, sqlite:///) have a second slash and do not match.
DRIVE = re.compile(
    r"(?i)(?<![A-Za-z])(?:[DE]:[\\/](?![\\/])|[A-Za-z]:[\\/](?![\\/])(?:Users|Temp|tmp|dsv|dstmp)(?:[\\/]|$))"
)
ENV_TEMP = re.compile(r"(?i)(%TEMP%|%TMP%|%LOCALAPPDATA%|environ(?:\.get)?\(\s*[\"'](?:TEMP|TMP|TMPDIR|LOCALAPPDATA)[\"'])")
SYSTEM_MARKERS = ("\\windows\\", "\\windows", "\\program files", "\\program files (x86)")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _allowlist() -> set[str]:
    if not ALLOWLIST.is_file():
        return set()
    allowed = set()
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().replace("\\", "/")
        if line:
            allowed.add(line)
    return allowed


def _skipped(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    if parts and parts[0] in SKIP_TREES:
        return True
    for part in parts:
        if part in SKIP_PARTS or part.startswith(SKIP_PREFIXES):
            return True
    return False


def _system_path(text: str) -> bool:
    normalized = text.replace("/", "\\").lower()
    return any(marker in normalized for marker in SYSTEM_MARKERS)


def _call_name(node: ast.AST) -> str | None:
    func = getattr(node, "func", None)
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name) and base.id == "tempfile":
            return func.attr
        return None
    if isinstance(func, ast.Name) and func.id in TEMP_CALLS:
        return func.id
    return None


def _has_dir(node: ast.Call) -> bool:
    return any(keyword.arg == "dir" for keyword in node.keywords)


def _scan_python(path: Path, text: str) -> list[str]:
    findings = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"{_rel(path)}:{exc.lineno}: cannot parse ({exc.msg})"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "gettempdir" or (name in TEMP_OK_WITH_DIR and not _has_dir(node)):
                findings.append(
                    f"{_rel(path)}:{node.lineno}: uncontrolled temporary path ({name})"
                )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if ENV_TEMP.search(node.value):
                findings.append(
                    f"{_rel(path)}:{node.lineno}: uncontrolled temporary path in a string"
                )
            for match in DRIVE.finditer(node.value):
                if not _system_path(match.group(0)):
                    findings.append(
                        f"{_rel(path)}:{node.lineno}: hardcoded external path {match.group(0)!r}"
                    )
    return findings


def _scan_text(path: Path, text: str) -> list[str]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if ENV_TEMP.search(line):
            findings.append(f"{_rel(path)}:{number}: uncontrolled temporary path")
        for match in DRIVE.finditer(line):
            if not _system_path(match.group(0)):
                findings.append(
                    f"{_rel(path)}:{number}: hardcoded external path {match.group(0)!r}"
                )
    return findings


def _scan_file(path: Path) -> list[str]:
    if _rel(path) in SELF_PATHS or _rel(path) in _allowlist():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    if path.suffix == ".py":
        return _scan_python(path, text)
    return _scan_text(path, text)


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _scan_links() -> list[str]:
    findings = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        current = Path(dirpath)
        kept = []
        for name in dirnames:
            child = current / name
            if name in SKIP_PARTS or name.startswith(SKIP_PREFIXES) or name in SKIP_TREES:
                continue
            if _is_link(child):
                target = child.resolve()
                try:
                    target.relative_to(ROOT)
                except ValueError:
                    findings.append(f"{_rel(child)}: link target leaves the root ({target})")
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            if _is_link(child):
                target = child.resolve()
                try:
                    target.relative_to(ROOT)
                except ValueError:
                    findings.append(f"{_rel(child)}: link target leaves the root ({target})")
    return findings


def main() -> int:
    findings: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        current = Path(dirpath)
        dirnames[:] = [
            name for name in dirnames
            if name not in SKIP_PARTS
            and name not in SKIP_TREES
            and not name.startswith(SKIP_PREFIXES)
        ]
        if _skipped(current) and current != ROOT:
            dirnames[:] = []
            continue
        for name in filenames:
            path = current / name
            if path.suffix.lower() not in SCAN_SUFFIXES or _skipped(path):
                continue
            findings.extend(_scan_file(path))
    findings.extend(_scan_links())
    if findings:
        print("root containment check failed:")
        for item in findings:
            print(f"  {item}")
        return 1
    print(f"root containment check passed ({ROOT})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
