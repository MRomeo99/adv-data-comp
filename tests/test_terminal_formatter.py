from __future__ import annotations


from adv_data_comp.formatters.terminal import TerminalFormatter
from adv_data_comp.models import (
    Anomaly,
    ComparisonMeta,
    ComparisonResult,
    FileMeta,
    Severity,
)


def _strip_ansi(text: str) -> str:
    """Re-render through a no_color Console-friendly strip for robust assertions."""
    from rich.text import Text

    return Text.from_ansi(text).plain


def _anomaly(layer, severity, column, message) -> Anomaly:
    return Anomaly(
        layer=layer,
        severity=severity,
        column=column,
        message=message,
        evidence={},
    )


def _meta(engine: str = "duckdb", runtime: float = 4.2) -> ComparisonMeta:
    return ComparisonMeta(
        comparison_id="test-uuid-1234",
        file_a=FileMeta(path="customers_jan.parquet", format="parquet", rows=50000, size_mb=45.0),
        file_b=FileMeta(path="customers_feb.csv", format="csv", rows=47153, size_mb=38.0),
        engine=engine,
        layers_run=["schema", "semantic", "statistical", "referential"],
        runtime_seconds=runtime,
    )


def _build_result_all_severities() -> ComparisonResult:
    anomalies = [
        _anomaly(
            "schema",
            Severity.CRITICAL,
            "customer_id",
            "Column `customer_id` type: int64 -> string",
        ),
        _anomaly(
            "referential",
            Severity.CRITICAL,
            "customer_id",
            "3,847 rows in file A not found in file B",
        ),
        _anomaly(
            "statistical",
            Severity.WARNING,
            "revenue",
            "null rate 2.1% -> 14.8% (+12.7%)",
        ),
        _anomaly(
            "semantic",
            Severity.WARNING,
            "cust_email",
            "cust_email <-> customer_email (similarity: 0.91)",
        ),
        _anomaly(
            "schema",
            Severity.INFO,
            "*",
            "Column order differs (3 columns repositioned)",
        ),
        _anomaly(
            "format",
            Severity.SUGGESTION,
            "full_name",
            "`full_name` in A may split into first/last in B",
        ),
    ]
    return ComparisonResult(anomalies=anomalies, meta=_meta())


def test_all_four_severities_render_in_fixed_order_with_counts():
    result = _build_result_all_severities()
    formatter = TerminalFormatter()

    output = formatter.format(result)
    plain = _strip_ansi(output)

    critical_idx = plain.index("CRITICAL (2)")
    warning_idx = plain.index("WARNING (2)")
    info_idx = plain.index("INFO (1)")
    suggestion_idx = plain.index("SUGGESTIONS (1)")

    assert critical_idx < warning_idx < info_idx < suggestion_idx

    assert "[Schema]" in plain
    assert "Column `customer_id` type: int64 -> string" in plain
    assert "3,847 rows in file A not found in file B" in plain
    assert "[Statistical]" in plain
    assert "null rate 2.1% -> 14.8% (+12.7%)" in plain
    assert "[Semantic]" in plain
    assert "[Referential]" in plain
    assert "[Format]" in plain


def test_zero_anomaly_severity_produces_no_section():
    # Only critical and info anomalies -- no warning, no suggestion.
    anomalies = [
        _anomaly("schema", Severity.CRITICAL, "customer_id", "Column type changed"),
        _anomaly("schema", Severity.INFO, "*", "Column order differs"),
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())
    formatter = TerminalFormatter()

    plain = _strip_ansi(formatter.format(result))

    assert "CRITICAL" in plain
    assert "INFO" in plain
    assert "WARNING" not in plain
    assert "SUGGESTIONS" not in plain


def test_zero_anomalies_total_shows_no_anomalies_message():
    result = ComparisonResult(anomalies=[], meta=_meta())
    formatter = TerminalFormatter()

    plain = _strip_ansi(formatter.format(result))

    assert "No anomalies found" in plain
    assert "CRITICAL" not in plain
    assert "WARNING" not in plain
    assert "INFO" not in plain
    assert "SUGGESTIONS" not in plain


def test_meta_none_does_not_crash_and_still_renders_anomalies():
    anomalies = [
        _anomaly("statistical", Severity.WARNING, "revenue", "null rate increased"),
    ]
    result = ComparisonResult(anomalies=anomalies, meta=None)
    formatter = TerminalFormatter()

    output = formatter.format(result)
    plain = _strip_ansi(output)

    assert "WARNING (1)" in plain
    assert "null rate increased" in plain
    assert "[Statistical]" in plain
    # No crash and no fabricated engine/runtime details.
    assert "Comparing:" not in plain


def test_summary_footer_includes_total_and_runtime_and_engine():
    result = _build_result_all_severities()
    formatter = TerminalFormatter()

    plain = _strip_ansi(formatter.format(result))

    assert "Total anomalies: 6" in plain
    assert "4.2s" in plain
    assert "duckdb" in plain


def test_format_returns_plain_string_type():
    result = _build_result_all_severities()
    formatter = TerminalFormatter()

    output = formatter.format(result)

    assert isinstance(output, str)
    assert len(output) > 0
