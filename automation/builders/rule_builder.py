"""Renders business-rule documents and the business-rule index."""

from __future__ import annotations

from ..core.models import BusinessRule
from ..templates.loader import render_template
from .markdown import MarkdownBuilder


class RuleBuilder:
    def render_rule(self, rule: BusinessRule) -> str:
        return render_template(
            "business_rule.md.tmpl",
            {
                "identifier": rule.identifier,
                "name": rule.name,
                "module": rule.module,
                "version": rule.version,
                "priority": rule.priority,
                "status": rule.status,
                "dependencies": ", ".join(rule.dependencies) or "—",
                "description": rule.description or "_To be documented._",
            },
        )

    def render_index(self, rules: list[BusinessRule], generated_on: str) -> str:
        md = MarkdownBuilder()
        md.heading("Business Rule Registry", 1)
        md.paragraph(
            "Auto-generated registry. Register a rule with "
            "`python -m automation.rules register`. "
            f"Last updated: {generated_on}."
        )
        if rules:
            md.table(
                ["ID", "Name", "Module", "Version", "Priority", "Status", "Dependencies"],
                [
                    [
                        f"[{r.identifier}]({r.path})" if r.path else r.identifier,
                        r.name,
                        r.module,
                        r.version,
                        r.priority,
                        r.status,
                        ", ".join(r.dependencies) or "—",
                    ]
                    for r in rules
                ],
            )
        else:
            md.paragraph("_No business rules registered yet._")
        return md.render()
