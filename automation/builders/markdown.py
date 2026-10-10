"""Small Markdown rendering helpers shared by the document builders."""

from __future__ import annotations

from collections.abc import Iterable


class MarkdownBuilder:
    """Accumulates Markdown lines and renders a final document string."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def line(self, text: str = "") -> "MarkdownBuilder":
        self._lines.append(text)
        return self

    def heading(self, text: str, level: int = 1) -> "MarkdownBuilder":
        self._lines.append(f"{'#' * level} {text}")
        self._lines.append("")
        return self

    def paragraph(self, text: str) -> "MarkdownBuilder":
        self._lines.append(text)
        self._lines.append("")
        return self

    def bullet(self, text: str) -> "MarkdownBuilder":
        self._lines.append(f"- {text}")
        return self

    def blank(self) -> "MarkdownBuilder":
        self._lines.append("")
        return self

    def table(self, headers: list[str], rows: Iterable[list[str]]) -> "MarkdownBuilder":
        self._lines.append("| " + " | ".join(headers) + " |")
        self._lines.append("|" + "|".join("---" for _ in headers) + "|")
        for row in rows:
            cells = [self._escape(str(c)) for c in row]
            self._lines.append("| " + " | ".join(cells) + " |")
        self._lines.append("")
        return self

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    def render(self) -> str:
        return "\n".join(self._lines).rstrip() + "\n"
