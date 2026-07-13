from __future__ import annotations

import json

from adv_data_comp.formatters.json_formatter import JsonFormatter
from adv_data_comp.models import (
    Anomaly,
    ComparisonMeta,
    ComparisonResult,
    FileMeta,
    Severity,
)


def _build_result() -> ComparisonResult:
    anomalies = [
        Anomaly(
            layer="schema",
            severity=Severity.CRITICAL,
            column="customer_id",
            message="Column type changed: int64 -> string",
            evidence={"type_a": "int64", "type_b": "string"},
        ),
        Anomaly(
            layer="statistical",
            severity=Severity.WARNING,
            column="revenue",
            message="Null rate increased from 2.1% to 14.8%",
            evidence={"null_rate_a": 0.021, "null_rate_b": 0.148},
            explanation="Likely a join failure upstream.",
        ),
        Anomaly(
            layer="semantic",
            severity=Severity.SUGGESTION,
            column="cust_email",
            message="Possible fuzzy match with customer_email",
            evidence={"similarity_score": 0.91},
        ),
    ]
    meta = ComparisonMeta(
        comparison_id="test-uuid-1234",
        file_a=FileMeta(path="a.parquet", format="parquet", rows=50000, size_mb=45.0),
        file_b=FileMeta(path="b.csv", format="csv", rows=47153, size_mb=38.0),
        engine="duckdb",
        layers_run=["schema", "semantic", "statistical"],
        runtime_seconds=4.2,
    )
    return ComparisonResult(anomalies=anomalies, meta=meta)


def test_json_formatter_round_trips_and_preserves_key_fields():
    result = _build_result()
    formatter = JsonFormatter()

    output = formatter.format(result)
    parsed = json.loads(output)

    assert parsed["comparison_id"] == "test-uuid-1234"
    assert parsed["engine"] == "duckdb"
    assert len(parsed["anomalies"]) == 3
    assert parsed["summary"] == {
        "critical": 1,
        "warning": 1,
        "info": 0,
        "suggestion": 1,
    }
    assert parsed["runtime_seconds"] == 4.2


def test_json_formatter_preserves_real_anomaly_fields():
    result = _build_result()
    formatter = JsonFormatter()

    parsed = json.loads(formatter.format(result))
    first = parsed["anomalies"][0]

    assert first["layer"] == "schema"
    assert first["severity"] == "critical"
    assert first["column"] == "customer_id"
    assert first["message"] == "Column type changed: int64 -> string"
    assert first["evidence"] == {"type_a": "int64", "type_b": "string"}
    assert first["explanation"] is None

    second = parsed["anomalies"][1]
    assert second["explanation"] == "Likely a join failure upstream."


def test_json_formatter_handles_missing_meta_without_crashing():
    result = ComparisonResult(
        anomalies=[
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column="signup_date",
                message="Date format differs",
                evidence={},
            )
        ],
        meta=None,
    )
    formatter = JsonFormatter()

    output = formatter.format(result)
    parsed = json.loads(output)

    assert parsed["comparison_id"] is None
    assert parsed["file_a"] is None
    assert parsed["file_b"] is None
    assert parsed["engine"] is None
    assert parsed["layers_run"] is None
    assert parsed["runtime_seconds"] is None
    assert len(parsed["anomalies"]) == 1
    assert parsed["summary"]["info"] == 1
