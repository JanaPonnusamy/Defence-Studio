"""Renders ADR documents and the ADR index."""

from __future__ import annotations

from ..core.models import AdrRecord
from ..templates.loader import render_template
from .markdown import MarkdownBuilder


class AdrBuilder:
    def render_adr(self, record: AdrRecord, deciders: str = "") -> str:
        return render_template(
            "adr.md.tmpl",
            {
                "identifier": record.identifier,
                "title": record.title,
                "status": record.status,
                "date": record.date,
                "deciders": deciders or "—",
            },
        )

    def render_index(self, records: list[AdrRecord], generated_on: str) -> str:
        md = MarkdownBuilder()
        md.heading("Architecture Decision Records", 1)
        md.paragraph(
            "Auto-generated index. Create a new ADR with "
            "`python -m automation adr new \"<title>\"`. "
            f"Last updated: {generated_on}."
        )
        if records:
            md.table(
                ["ADR", "Title", "Status", "Date"],
                [
                    [r.identifier, f"[{r.title}]({r.path})" if r.path else r.title,
                     r.status, r.date or "—"]
                    for r in records
                ],
            )
        else:
            md.paragraph("_No ADRs recorded yet._")
        return md.render()
