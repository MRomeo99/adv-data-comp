"""Self-contained HTML report formatter.

Renders a :class:`~adv_data_comp.models.ComparisonResult` as a single,
dependency-free HTML string: all CSS is inlined in a ``<style>`` tag, there
are no external requests, and the only "interactivity" is the native
``<details>``/``<summary>`` expand/collapse behaviour on anomaly cards (zero
JavaScript required for that). Statistical charts are raw inline ``<svg>``
markup built by hand — no charting library, no JS.

Scoping decision (read this before extending this module)
-----------------------------------------------------------
CLAUDE.md describes the HTML report as including a "side-by-side schema
comparison table" and "statistical profile charts", phrased as if a full
per-column schema/profile dump were available for *every* column in both
files. It is not: ``ComparisonResult`` only carries ``anomalies`` (each with
a freeform ``evidence: dict``) and optional ``meta``. There is no
``ColumnProfile`` list attached to the result, so a full schema/profile dump
cannot be reconstructed here without fabricating data for columns that never
triggered an anomaly.

This formatter therefore derives both sections *only* from anomalies already
present on the result:

- **Schema comparison table**: built exclusively from ``layer == "schema"``
  anomalies — one row per anomaly, showing the column name plus whatever
  evidence keys that anomaly happens to carry (e.g. ``type_a``/``type_b``,
  ``null_rate_a``/``null_rate_b``). Columns with no schema anomaly are not
  listed as "matching" rows because we have no evidence about them at all.
  The whole section is omitted when there are no schema-layer anomalies.

- **Statistical charts**: for ``layer == "statistical"`` anomalies, we scan
  the anomaly's ``evidence`` dict for a "clean numeric pair" — a key ending
  in ``_a`` (e.g. ``mean_a``) with a matching ``_b`` key (``mean_b``) whose
  values are both plain numbers (not booleans). When found, we render one
  simple two-bar inline SVG (File A vs. File B), with bar length proportional
  to the larger of the two values. Anomalies without such a pair are skipped
  — no chart is fabricated for them. The whole section is omitted when no
  statistical anomaly qualifies.

CLAUDE.md also mentions a "column mapping visualization (shows fuzzy
matches)". That was explicitly out of scope for this implementation pass
(see the task instructions this module was built against) for the same
reason: ``Anomaly`` has a single ``column`` field and a freeform
``evidence`` dict, not guaranteed ``column_a``/``column_b``/
``similarity_score`` fields, so a dedicated mapping visualization is left
for a follow-up rather than guessed at here.

Escaping approach
------------------
All user-controlled string content — anomaly ``column``, ``message``,
``explanation``, and every evidence key/value — is passed through
``html.escape()`` before being interpolated into the page. The page is built
via plain f-strings (no templating engine) specifically so every
interpolation point is a single, auditable ``esc(...)`` call. This guarantees
a malicious column name or evidence value (e.g. containing ``<script>``)
can never break out of its container element or inject markup.
"""

from __future__ import annotations

import html
import json
from typing import Any

from adv_data_comp.formatters.base import AbstractFormatter
from adv_data_comp.models import Anomaly, ComparisonResult

_SEVERITY_ORDER = ["critical", "warning", "info", "suggestion"]
_SEVERITY_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "info": "Info",
    "suggestion": "Suggestion",
}


def esc(value: Any) -> str:
    """Escape any value for safe interpolation into HTML text/attributes."""
    return html.escape(str(value), quote=True)


def _find_numeric_pair(evidence: dict[str, Any]) -> tuple[str, float, float] | None:
    """Find the first `<name>_a` / `<name>_b` pair of plain numeric values."""
    for key in sorted(evidence.keys()):
        if not key.endswith("_a"):
            continue
        base = key[: -len("_a")]
        b_key = f"{base}_b"
        if b_key not in evidence:
            continue
        a_val, b_val = evidence[key], evidence[b_key]
        if _is_plain_number(a_val) and _is_plain_number(b_val):
            return base, float(a_val), float(b_val)
    return None


def _is_plain_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _svg_bar_chart(stat_name: str, val_a: float, val_b: float) -> str:
    bar_max_width = 260.0
    max_val = max(abs(val_a), abs(val_b)) or 1.0
    width_a = abs(val_a) / max_val * bar_max_width
    width_b = abs(val_b) / max_val * bar_max_width
    label = esc(stat_name)
    return (
        '<svg viewBox="0 0 380 64" width="380" height="64" role="img" '
        f'aria-label="{label} comparison" class="stat-chart">'
        f'<text x="0" y="12" font-size="11" class="chart-label">{label}</text>'
        f'<text x="0" y="30" font-size="10" class="chart-text">A: {esc(val_a)}</text>'
        f'<rect x="70" y="20" width="{width_a:.1f}" height="12" class="bar-a"/>'
        f'<text x="0" y="52" font-size="10" class="chart-text">B: {esc(val_b)}</text>'
        f'<rect x="70" y="42" width="{width_b:.1f}" height="12" class="bar-b"/>'
        "</svg>"
    )


def _render_evidence(evidence: dict[str, Any]) -> str:
    if not evidence:
        return ""
    pretty = json.dumps(evidence, indent=2, default=str, sort_keys=True)
    return f'<pre class="evidence">{esc(pretty)}</pre>'


def _render_explanation(explanation: str | None) -> str:
    if not explanation:
        return ""
    return (
        '<div class="explanation-panel">'
        '<div class="explanation-label">AI Explanation</div>'
        f"<p>{esc(explanation)}</p>"
        "</div>"
    )


def _render_card(anomaly: Anomaly) -> str:
    severity = anomaly.severity.value if hasattr(anomaly.severity, "value") else str(anomaly.severity)
    summary_text = f"[{esc(anomaly.layer)}] {esc(anomaly.column)}: {esc(anomaly.message)}"
    body = _render_evidence(anomaly.evidence) + _render_explanation(anomaly.explanation)
    return (
        f'<details class="anomaly-card severity-{esc(severity)}">'
        f"<summary>{summary_text}</summary>"
        f'<div class="anomaly-body">{body}</div>'
        "</details>"
    )


def _render_anomaly_sections(result: ComparisonResult) -> str:
    if not result.anomalies:
        return '<p class="no-anomalies">No anomalies found. The two files match on every layer that was run.</p>'

    by_severity: dict[str, list[Anomaly]] = {sev: [] for sev in _SEVERITY_ORDER}
    for anomaly in result.anomalies:
        sev = anomaly.severity.value if hasattr(anomaly.severity, "value") else str(anomaly.severity)
        by_severity.setdefault(sev, []).append(anomaly)

    sections = []
    for sev in _SEVERITY_ORDER:
        anomalies = by_severity.get(sev, [])
        if not anomalies:
            continue
        label = _SEVERITY_LABELS.get(sev, sev.title())
        cards = "".join(_render_card(a) for a in anomalies)
        sections.append(
            f'<section class="severity-group severity-group-{esc(sev)}">'
            f"<h2>{esc(label)} ({len(anomalies)})</h2>"
            f"{cards}"
            "</section>"
        )
    return "".join(sections)


def _render_schema_table(result: ComparisonResult) -> str:
    schema_anomalies = [a for a in result.anomalies if a.layer == "schema"]
    if not schema_anomalies:
        return ""

    evidence_keys: list[str] = []
    for a in schema_anomalies:
        for key in a.evidence.keys():
            if key not in evidence_keys:
                evidence_keys.append(key)
    evidence_keys.sort()

    header_cells = "".join(f"<th>{esc(k)}</th>" for k in evidence_keys)
    rows = []
    for a in schema_anomalies:
        cells = "".join(f"<td>{esc(a.evidence.get(k, '—'))}</td>" for k in evidence_keys)
        rows.append(f"<tr><td>{esc(a.column)}</td>{cells}</tr>")

    return (
        '<section class="schema-table-section">'
        "<h2>Side-by-side Schema Comparison</h2>"
        '<table class="schema-table">'
        f"<thead><tr><th>Column</th>{header_cells}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _render_stat_charts(result: ComparisonResult) -> str:
    charts = []
    for a in result.anomalies:
        if a.layer != "statistical":
            continue
        pair = _find_numeric_pair(a.evidence)
        if pair is None:
            continue
        stat_name, val_a, val_b = pair
        svg = _svg_bar_chart(stat_name, val_a, val_b)
        charts.append(
            '<div class="stat-chart-card">'
            f"<h3>{esc(a.column)}</h3>"
            f"{svg}"
            "</div>"
        )
    if not charts:
        return ""
    return (
        '<section class="stat-charts-section">'
        "<h2>Statistical Profile Charts</h2>"
        f"{''.join(charts)}"
        "</section>"
    )


def _render_header(result: ComparisonResult) -> str:
    summary = result.summary
    badges = "".join(
        f'<span class="badge badge-{esc(sev)}">{esc(_SEVERITY_LABELS.get(sev, sev.title()))}: '
        f"{summary.get(sev, 0)}</span>"
        for sev in _SEVERITY_ORDER
    )

    meta = result.meta
    if meta is None:
        return (
            '<header class="summary-header">'
            "<h1>Comparison Report</h1>"
            f'<div class="badges">{badges}</div>'
            "</header>"
        )

    file_a, file_b = meta.file_a, meta.file_b
    return (
        '<header class="summary-header">'
        "<h1>Comparison Report</h1>"
        f"<p><strong>Comparison ID:</strong> {esc(meta.comparison_id)}</p>"
        '<table class="file-meta-table">'
        "<thead><tr><th></th><th>File A</th><th>File B</th></tr></thead>"
        "<tbody>"
        f"<tr><td>Path</td><td>{esc(file_a.path)}</td><td>{esc(file_b.path)}</td></tr>"
        f"<tr><td>Format</td><td>{esc(file_a.format)}</td><td>{esc(file_b.format)}</td></tr>"
        f"<tr><td>Rows</td><td>{esc(file_a.rows)}</td><td>{esc(file_b.rows)}</td></tr>"
        f"<tr><td>Size (MB)</td><td>{esc(file_a.size_mb)}</td><td>{esc(file_b.size_mb)}</td></tr>"
        "</tbody>"
        "</table>"
        f"<p><strong>Engine:</strong> {esc(meta.engine)} &nbsp;&nbsp; "
        f"<strong>Runtime:</strong> {esc(meta.runtime_seconds)}s</p>"
        f'<div class="badges">{badges}</div>'
        "</header>"
    )


_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff;
  --fg: #1b1f24;
  --muted: #6b7280;
  --border: #d0d7de;
  --card-bg: #f6f8fa;
  --critical: #cf222e;
  --warning: #9a6700;
  --info: #0969da;
  --suggestion: #6639ba;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --fg: #e6edf3;
    --muted: #9198a1;
    --border: #30363d;
    --card-bg: #161b22;
  }
}
body {
  font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--fg);
  margin: 0;
  padding: 1.5rem;
  line-height: 1.5;
}
h1, h2, h3 { margin-top: 0; }
table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; }
th, td {
  border: 1px solid var(--border);
  padding: 0.4rem 0.6rem;
  text-align: left;
  font-size: 0.9rem;
}
.badges { margin-top: 0.75rem; }
.badge {
  display: inline-block;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.15rem 0.75rem;
  margin-right: 0.5rem;
  font-size: 0.85rem;
}
.badge-critical { color: var(--critical); }
.badge-warning { color: var(--warning); }
.badge-info { color: var(--info); }
.badge-suggestion { color: var(--suggestion); }
.severity-group { margin: 1.25rem 0; }
.anomaly-card {
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-radius: 6px;
  background: var(--card-bg);
  margin-bottom: 0.6rem;
  padding: 0.5rem 0.75rem;
}
.severity-critical { border-left-color: var(--critical); }
.severity-warning { border-left-color: var(--warning); }
.severity-info { border-left-color: var(--info); }
.severity-suggestion { border-left-color: var(--suggestion); }
.anomaly-card summary { cursor: pointer; font-weight: 600; }
.anomaly-body { margin-top: 0.5rem; }
.evidence {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.5rem;
  overflow-x: auto;
  font-size: 0.85rem;
}
.explanation-panel {
  border: 1px solid var(--info);
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
  margin-top: 0.5rem;
  background: var(--card-bg);
}
.explanation-label {
  font-weight: 700;
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--info);
  margin-bottom: 0.25rem;
}
.no-anomalies {
  font-size: 1.1rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card-bg);
}
.stat-chart-card {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.6rem;
  background: var(--card-bg);
}
.chart-label, .chart-text { fill: var(--fg); }
.bar-a { fill: #4c78a8; }
.bar-b { fill: #f58518; }
"""


class HtmlFormatter(AbstractFormatter):
    """Renders a ComparisonResult as one self-contained HTML report."""

    def format(self, result: ComparisonResult) -> str:
        header = _render_header(result)
        anomaly_sections = _render_anomaly_sections(result)
        schema_table = _render_schema_table(result)
        stat_charts = _render_stat_charts(result)

        return (
            "<!doctype html>"
            '<html lang="en">'
            "<head>"
            '<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>Comparison Report</title>"
            f"<style>{_STYLE}</style>"
            "</head>"
            "<body>"
            f"{header}"
            "<main>"
            f"{anomaly_sections}"
            f"{schema_table}"
            f"{stat_charts}"
            "</main>"
            "</body>"
            "</html>"
        )
