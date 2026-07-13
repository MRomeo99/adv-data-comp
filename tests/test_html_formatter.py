from __future__ import annotations

from html.parser import HTMLParser

from adv_data_comp.formatters.html_formatter import HtmlFormatter
from adv_data_comp.models import (
    Anomaly,
    ComparisonMeta,
    ComparisonResult,
    FileMeta,
)


def _anomaly(
    layer: str = "statistical",
    severity: str = "warning",
    column: str = "revenue",
    message: str = "revenue anomaly",
    evidence: dict | None = None,
    explanation: str | None = None,
) -> Anomaly:
    return Anomaly(
        layer=layer,
        severity=severity,
        column=column,
        message=message,
        evidence=evidence or {},
        explanation=explanation,
    )


def _meta() -> ComparisonMeta:
    return ComparisonMeta(
        comparison_id="cmp-123",
        file_a=FileMeta(path="a.parquet", format="parquet", rows=1000, size_mb=12.5),
        file_b=FileMeta(path="b.csv", format="csv", rows=980, size_mb=10.1),
        engine="polars",
        layers_run=["format", "schema", "semantic", "statistical", "referential"],
        runtime_seconds=1.23,
    )


def _assert_parses_as_html(html: str) -> None:
    # Should not raise — a lenient structural sanity check.
    HTMLParser().feed(html)


class TestHtmlFormatterStructure:
    def test_produces_parseable_html(self):
        result = ComparisonResult(anomalies=[_anomaly()], meta=_meta())
        html = HtmlFormatter().format(result)

        _assert_parses_as_html(html)
        assert html.strip().lower().startswith("<!doctype html>")
        assert "<html" in html
        assert "</html>" in html
        assert "<style>" in html

    def test_zero_anomalies_produces_valid_page_with_no_anomalies_message(self):
        result = ComparisonResult(anomalies=[], meta=_meta())
        html = HtmlFormatter().format(result)

        _assert_parses_as_html(html)
        assert "no anomalies found" in html.lower()

    def test_no_meta_renders_simpler_header_without_crashing(self):
        result = ComparisonResult(anomalies=[_anomaly()], meta=None)
        html = HtmlFormatter().format(result)

        _assert_parses_as_html(html)
        assert "<html" in html


class TestHtmlFormatterSummaryHeader:
    def test_severity_counts_and_file_paths_appear(self):
        meta = _meta()
        result = ComparisonResult(
            anomalies=[
                _anomaly(severity="critical", column="customer_id"),
                _anomaly(severity="warning", column="revenue"),
                _anomaly(severity="info", column="signup_date"),
                _anomaly(severity="suggestion", column="full_name"),
            ],
            meta=meta,
        )
        html = HtmlFormatter().format(result)

        assert "cmp-123" in html
        assert "a.parquet" in html
        assert "b.csv" in html
        assert "polars" in html
        # summary counts: one of each severity
        summary = result.summary
        assert str(summary["critical"]) in html
        assert str(summary["warning"]) in html
        assert str(summary["info"]) in html
        assert str(summary["suggestion"]) in html


class TestHtmlFormatterAnomalyCards:
    def test_cards_are_expandable_details_elements(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(
                    layer="statistical",
                    severity="critical",
                    column="revenue",
                    message="null rate spiked",
                    evidence={"null_rate_a": 0.02, "null_rate_b": 0.15},
                )
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "<details" in html
        assert "<summary" in html
        assert "revenue" in html
        assert "null rate spiked" in html
        assert "[statistical]" in html.lower()

    def test_critical_anomalies_render_before_warning(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(severity="warning", column="warn_col", message="a warning"),
                _anomaly(severity="critical", column="crit_col", message="a critical issue"),
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert html.index("crit_col") < html.index("warn_col")


class TestHtmlFormatterSchemaTable:
    def test_schema_anomaly_triggers_schema_table(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(
                    layer="schema",
                    severity="warning",
                    column="customer_id",
                    message="type mismatch",
                    evidence={"type_a": "int64", "type_b": "string"},
                )
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "schema comparison" in html.lower()
        assert "int64" in html
        assert "string" in html

    def test_non_schema_result_omits_schema_table(self):
        result = ComparisonResult(
            anomalies=[_anomaly(layer="statistical", severity="warning", column="revenue")],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "schema comparison" not in html.lower()


class TestHtmlFormatterStatCharts:
    def test_numeric_pair_evidence_produces_inline_svg(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(
                    layer="statistical",
                    severity="warning",
                    column="revenue",
                    message="mean shifted",
                    evidence={"mean_a": 10.5, "mean_b": 15.2},
                )
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "<svg" in html
        assert "<rect" in html

    def test_non_numeric_evidence_produces_no_svg_chart(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(
                    layer="statistical",
                    severity="info",
                    column="signup_date",
                    message="format differs",
                    evidence={"format_a": "MM/DD/YYYY", "format_b": "YYYY-MM-DD"},
                )
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "<svg" not in html


class TestHtmlFormatterExplanationPanel:
    def test_explanation_renders_distinct_panel(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(
                    severity="critical",
                    column="revenue",
                    message="null rate spiked",
                    explanation="This likely indicates an upstream join failure.",
                )
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "ai explanation" in html.lower()
        assert "This likely indicates an upstream join failure." in html

    def test_no_explanation_no_panel(self):
        result = ComparisonResult(
            anomalies=[_anomaly(explanation=None)],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "ai explanation" not in html.lower()


class TestHtmlFormatterEscaping:
    def test_script_tag_in_column_name_is_escaped(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(
                    column="<script>alert(1)</script>",
                    message="evil column",
                )
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_script_tag_in_message_and_evidence_is_escaped(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly(
                    column="revenue",
                    message="<img src=x onerror=alert(1)>",
                    evidence={"value_a": "<script>bad()</script>"},
                )
            ],
            meta=_meta(),
        )
        html = HtmlFormatter().format(result)

        assert "<img src=x onerror=alert(1)>" not in html
        assert "<script>bad()</script>" not in html
        _assert_parses_as_html(html)
