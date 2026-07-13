from adv_data_comp.formatters.markdown_formatter import MarkdownFormatter
from adv_data_comp.models import Anomaly, ComparisonMeta, ComparisonResult, FileMeta


def _anomaly(severity: str, column: str = "revenue", layer: str = "statistical") -> Anomaly:
    return Anomaly(
        layer=layer,
        severity=severity,
        column=column,
        message=f"{column} anomaly ({severity})",
        evidence={"value_a": 1, "value_b": 2},
    )


def _meta() -> ComparisonMeta:
    return ComparisonMeta(
        comparison_id="abc-123",
        file_a=FileMeta(path="customers_jan.parquet", format="parquet", rows=50000, size_mb=45.0),
        file_b=FileMeta(path="customers_feb.csv", format="csv", rows=47153, size_mb=38.0),
        engine="duckdb",
        layers_run=["format", "schema", "semantic", "statistical", "referential"],
        runtime_seconds=4.2,
    )


class TestMarkdownFormatter:
    def test_header_includes_file_a_and_file_b_when_meta_present(self):
        result = ComparisonResult(anomalies=[_anomaly("critical")], meta=_meta())

        output = MarkdownFormatter().format(result)

        assert "customers_jan.parquet" in output
        assert "customers_feb.csv" in output

    def test_omits_header_gracefully_when_meta_is_none(self):
        result = ComparisonResult(anomalies=[_anomaly("critical")], meta=None)

        output = MarkdownFormatter().format(result)

        assert output  # doesn't crash, still produces output

    def test_summary_line_has_emoji_per_severity(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly("critical"),
                _anomaly("warning"),
                _anomaly("info"),
                _anomaly("suggestion"),
            ]
        )

        output = MarkdownFormatter().format(result)

        assert "🔴" in output
        assert "🟡" in output
        assert "🔵" in output
        assert "💡" in output

    def test_critical_section_appears_before_warning_info_suggestion(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly("suggestion", column="full_name"),
                _anomaly("info", column="signup_date"),
                _anomaly("warning", column="revenue"),
                _anomaly("critical", column="customer_id"),
            ]
        )

        output = MarkdownFormatter().format(result)

        critical_pos = output.index("customer_id")
        warning_pos = output.index("revenue")
        info_pos = output.index("signup_date")
        suggestion_pos = output.index("full_name")

        assert critical_pos < warning_pos < info_pos < suggestion_pos

    def test_anomaly_tables_have_layer_column_message_columns_and_omit_evidence(self):
        result = ComparisonResult(anomalies=[_anomaly("critical", column="customer_id")])

        output = MarkdownFormatter().format(result)

        assert "Layer" in output
        assert "Column" in output
        assert "Message" in output
        assert "customer_id" in output
        assert "value_a" not in output  # raw evidence dict keys should not leak in

    def test_empty_severity_sections_are_omitted(self):
        result = ComparisonResult(anomalies=[_anomaly("critical", column="customer_id")])

        output = MarkdownFormatter().format(result)

        assert "🟡" not in output
        assert "🔵" not in output
        assert "💡" not in output

    def test_zero_anomalies_produces_clear_no_anomalies_message(self):
        result = ComparisonResult(anomalies=[])

        output = MarkdownFormatter().format(result)

        assert "no anomalies" in output.lower()
        assert "|" not in output  # no empty GFM tables
