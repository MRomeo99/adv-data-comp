from __future__ import annotations

from adv_data_comp.formatters.base import AbstractFormatter
from adv_data_comp.models import Anomaly, ComparisonResult, Severity

_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.WARNING: "🟡",
    Severity.INFO: "🔵",
    Severity.SUGGESTION: "💡",
}

_LABEL = {
    Severity.CRITICAL: "Critical",
    Severity.WARNING: "Warning",
    Severity.INFO: "Info",
    Severity.SUGGESTION: "Suggestion",
}

# Critical always first, then warning, info, suggestion.
_SEVERITY_ORDER = [Severity.CRITICAL, Severity.WARNING, Severity.INFO, Severity.SUGGESTION]


class MarkdownFormatter(AbstractFormatter):
    """Renders a ComparisonResult as GitHub-flavored Markdown for PR comments."""

    def format(self, result: ComparisonResult) -> str:
        lines: list[str] = []

        header = self._header(result)
        if header:
            lines.append(header)
            lines.append("")

        if not result.anomalies:
            lines.append("**No anomalies found.** ✅")
            return "\n".join(lines) + "\n"

        lines.append(self._summary_line(result))
        lines.append("")

        by_severity: dict[Severity, list[Anomaly]] = {severity: [] for severity in _SEVERITY_ORDER}
        for anomaly in result.anomalies:
            by_severity[anomaly.severity].append(anomaly)

        for severity in _SEVERITY_ORDER:
            anomalies = by_severity[severity]
            if not anomalies:
                continue
            lines.append(f"### {_EMOJI[severity]} {_LABEL[severity]} ({len(anomalies)})")
            lines.append("")
            lines.append("| Layer | Column | Message |")
            lines.append("| --- | --- | --- |")
            for anomaly in anomalies:
                lines.append(f"| {anomaly.layer} | {anomaly.column} | {anomaly.message} |")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _header(self, result: ComparisonResult) -> str:
        if result.meta is None:
            return ""
        meta = result.meta
        return f"## Comparing: `{meta.file_a.path}` → `{meta.file_b.path}`"

    def _summary_line(self, result: ComparisonResult) -> str:
        counts = result.summary
        parts = [
            f"{_EMOJI[severity]} {_LABEL[severity]}: {counts[severity.value]}"
            for severity in _SEVERITY_ORDER
            if counts[severity.value] > 0
        ]
        return "**Summary:** " + "  ".join(parts)
