"""Inventory Defence Era-looking resources outside the project root.

This script only reads and writes the inventory under ``docs/root_containment``.
It does not copy, move, or delete anything. Secret values are not recorded.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "root_containment" / "EXTERNAL_RESOURCE_INVENTORY.md"
NAME_RE = re.compile(r"defence|defenceera|defence-studio|defencestudio", re.I)
SECRET_RE = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL", re.I)
SKIP_DESCEND = {
    "Windows", "Program Files", "Program Files (x86)", "node_modules",
    "$Recycle.Bin", "System Volume Information", ".git", "Packages",
    "Microsoft", "Google", "npm", "pip", "pnpm", "Temp", "Programs",
    "Package Cache", "CrashDumps",
}


def _classify(path: Path) -> str:
    text = str(path).lower()
    name = path.name.lower()
    if name in {"dsv", "dstmp"} or name.startswith("defence-backend-tests"):
        return "stale test scratch (not moved)"
    if any(part in text for part in ("\\windows\\", "\\program files", "\\python", "\\git\\")):
        return "external system dependency"
    if any(part in text for part in ("\\.cursor\\", "\\.claude\\", "\\appdata\\", "\\.codex\\")):
        return "external system dependency (IDE or agent host metadata; do not move)"
    if NAME_RE.search(path.name) or "defence" in text:
        return "Defence Era-owned candidate (not moved)"
    return "needs review"


def _scan(path: Path, depth: int, hits: list[Path], errors: list[str]) -> None:
    if depth < 0 or not path.exists():
        return
    try:
        children = list(path.iterdir())
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return
    for child in children:
        if NAME_RE.search(child.name):
            hits.append(child)
        if not child.is_dir() or depth == 0 or child.name in SKIP_DESCEND:
            continue
        try:
            if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                continue
        except OSError:
            continue
        _scan(child, depth - 1, hits, errors)


def _known(hits: list[Path]) -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    candidates = [
        Path("E:/DefenceStudio-Components"),
        Path("E:/Jana Soft"),
        Path("E:/Jana Soft/defence-align"),
        Path("E:/Jana Soft/DefenceStudio"),
        Path("C:/dsv"),
        Path("C:/dstmp"),
        home / "Desktop",
        home / "Documents",
        home / ".codex" / "artifacts",
        Path(r"C:\Users\Pharma\.codex\artifacts"),
    ]
    for candidate in candidates:
        if candidate.exists() and NAME_RE.search(str(candidate)):
            hits.append(candidate)
        elif candidate.exists() and candidate.name.lower() in {"dsv", "dstmp", "artifacts", "jana soft"}:
            hits.append(candidate)


def _env_rows() -> list[str]:
    rows = []
    for key, value in sorted(os.environ.items()):
        if "DEFENCE" not in key and "STUDIO" not in key:
            continue
        if SECRET_RE.search(key):
            rows.append(f"| `{key}` | set | not recorded |")
            continue
        path = Path(value)
        if value and (path.is_absolute() or "\\" in value or "/" in value):
            location = "inside root" if _inside(path) else "outside root"
            rows.append(f"| `{key}` | `{value}` | {location} |")
        else:
            rows.append(f"| `{key}` | `{value}` | not a path |")
    return rows or ["| (none) | | |"]


def _inside(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


def _sha256(path: Path) -> str | None:
    import hashlib
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _review_notes() -> str:
    lines = []
    for scratch in (Path("C:/dsv"), Path("C:/dstmp")):
        if not scratch.exists():
            lines.append(f"- `{scratch}` is absent.")
            continue
        try:
            count = sum(1 for _ in scratch.iterdir())
        except OSError as exc:
            count = f"unreadable ({exc})"
        lines.append(
            f"- `{scratch}` is stale test scratch ({count} top-level entries). "
            "It is not project source and was not copied."
        )
    sibling = Path(r"D:\jana soft\DefenceEra-TTS-Development")
    if sibling.is_dir():
        try:
            names = ", ".join(sorted(child.name for child in sibling.iterdir())[:12])
        except OSError as exc:
            names = f"unreadable ({exc})"
        lines.append(
            f"- `{sibling}` is a sibling directory ({names}). "
            "It was not moved; a bulk copy of a second tree is outside this pass."
        )
    copies = Path("D:/rsw1")
    if copies.is_dir():
        for child in sorted(copies.glob("DEFENCE*")):
            local = ROOT / child.name
            if not local.is_file():
                lines.append(f"- `{child}` has no same-named file in the root. Not copied.")
                continue
            same = _sha256(child) == _sha256(local)
            state = "byte-identical to the in-repo file" if same else "differs from the in-repo file"
            lines.append(f"- `{child}` {state}. Not copied and the in-repo file was not overwritten.")
    return "\n".join(lines) or "- None."


def _scheduled_tasks() -> list[str]:
    try:
        import subprocess
        completed = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"Scheduled-task query failed: {exc}"]
    found = []
    for line in completed.stdout.splitlines():
        if NAME_RE.search(line):
            found.append(line)
    return found or ["No scheduled task name matched Defence Era."]


def main() -> int:
    home = Path(os.environ.get("USERPROFILE", ""))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    temp = Path(os.environ.get("TEMP", ""))
    hits: list[Path] = []
    errors: list[str] = []
    for folder, depth in (
        (home / "Desktop", 2),
        (home / "Documents", 2),
        (home / "Downloads", 1),
        (home, 1),
        (local, 2),
        (roaming, 1),
        (temp, 1),
        (Path("D:/"), 1),
        (Path("E:/"), 1),
        (Path("C:/"), 1),
    ):
        _scan(folder, depth, hits, errors)
    _known(hits)
    unique: list[Path] = []
    seen = set()
    for hit in hits:
        key = str(hit).lower()
        if key in seen or _inside(hit):
            continue
        seen.add(key)
        unique.append(hit)
    unique.sort(key=lambda item: str(item).lower())

    rows = []
    for hit in unique:
        kind = "directory" if hit.is_dir() else "file"
        rows.append(f"| `{hit}` | {kind} | {_classify(hit)} | not moved |")
    if not rows:
        rows.append("| (none) | | | |")

    env_rows = _env_rows()
    tasks = _scheduled_tasks()
    error_lines = "\n".join(f"- {item}" for item in errors) or "- None."
    task_lines = "\n".join(f"- {item}" for item in tasks)
    text = f"""# External resource inventory

Generated by `scripts/inventory_external_resources.py`. This run did not copy,
move, or delete any file. A row classified as a Defence Era-owned candidate
stays where it is until a later, verified migration group.

Canonical root: `{ROOT}`

## Name matches outside the root

Search was depth-limited (Desktop, Documents, Downloads, the user profile,
AppData, Temp, and the top level of C:, D:, and E:). It also checked known
historical locations from earlier project notes.

| Original path | Kind | Classification | Action |
|---|---|---|---|
{chr(10).join(rows)}

## Defence Era environment variables in this process

Secret-like values are recorded only as "set".

| Name | Value | Location |
|---|---|---|
{chr(10).join(env_rows)}

## Scheduled tasks

{task_lines}

## Read errors

{error_lines}

## Review of this run

{_review_notes()}

## Not moved on purpose

- Windows, Python, Git, Node, FFmpeg, Chrome, and system fonts stay installed
  outside the root. They are external system dependencies.
- Cursor, Claude, and Codex host metadata under the user profile stays there.
  Project-owned agent notes already live in `AGENT_WORK`.
- Virtual environments and package caches inside the root stay untracked.
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(unique)} outside hits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
