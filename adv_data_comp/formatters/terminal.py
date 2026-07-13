from __future__ import annotations

import io

from rich.console import Console

from adv_data_comp.formatters.base import AbstractFormatter
from adv_data_comp.models import ComparisonResult, Severity

# Fixed rendering order: critical -> warning -> info -> suggestion.
_SEVERITY_SECTIONS: list[tuple[Severity, str, str]] = [
    (Severity.CRITICAL, "🔴 CRITICAL", "bold red"),
    (Severity.WARNING, "🟡 WARNING", "bold yellow"),
    (Severity.INFO, "🔵 INFO", "bold cyan"),
    (Severity.SUGGESTION, "💡 SUGGESTIONS", "bold magenta"),
]


class TerminalFormatter(AbstractFormatter):
    """Renders a ComparisonResult as Rich-formatted terminal output (as a string)."""

    def format(self, result: ComparisonResult) -> str:
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)

        self._render_header(console, result)
        console.print()

        if not result.anomalies:
            console.print("[bold green]No anomalies found[/bold green] — files match.")
        else:
            self._render_sections(console, result)

        console.print()
        self._render_footer(console, result)

        return buffer.getvalue()

    def _render_header(self, console: Console, result: ComparisonResult) -> None:
        console.rule("[bold]adv-data-comp[/bold]", align="left")
        meta = result.meta
        if meta is not None:
            console.print(f"Comparing: {meta.file_a.path} → {meta.file_b.path}")
            console.print(f"Engine: {meta.engine}")
        console.rule()

    def _render_sections(self, console: Console, result: ComparisonResult) -> None:
        by_severity: dict[Severity, list] = {severity: [] for severity, _, _ in _SEVERITY_SECTIONS}
        for anomaly in result.anomalies:
            by_severity[anomaly.severity].append(anomaly)

        first_section = True
        for severity, label, style in _SEVERITY_SECTIONS:
            anomalies = by_severity[severity]
            if not anomalies:
                continue
            if not first_section:
                console.print()
            first_section = False

            console.print(f"[{style}]{label} ({len(anomalies)})[/{style}]")
            for anomaly in anomalies:
                layer_label = anomaly.layer.title()
                # markup=False: anomaly text legitimately contains literal
                # "[Layer]" brackets which must not be parsed as Rich style tags.
                console.print(f"  [{layer_label}] {anomaly.message}", markup=False)

    def _render_footer(self, console: Console, result: ComparisonResult) -> None:
        console.rule()
        total = len(result.anomalies)
        meta = result.meta
        if meta is not None:
            console.print(
                f"Total anomalies: {total}  │  "
                f"Runtime: {meta.runtime_seconds:.1f}s  │  Engine: {meta.engine}"
            )
        else:
            console.print(f"Total anomalies: {total}")
