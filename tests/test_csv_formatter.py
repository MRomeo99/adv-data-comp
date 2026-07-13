import csv
import io
import json

from adv_data_comp.formatters.csv_formatter import CsvFormatter
from adv_data_comp.models import Anomaly, ComparisonResult


def _anomaly(severity: str, column: str = "revenue", **evidence) -> Anomaly:
    return Anomaly(
        layer="statistical",
        severity=severity,
        column=column,
        message=f"{column} anomaly ({severity})",
        evidence=evidence or {"value_a": 1, "value_b": 2},
        explanation=None,
    )


class TestCsvFormatter:
    def test_returns_one_row_per_anomaly_with_expected_columns(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly("critical", column="customer_id", delta=0.5),
                _anomaly("warning", column="revenue", value_a=0.021, value_b=0.148),
                _anomaly("info", column="signup_date"),
                _anomaly("suggestion", column="full_name"),
            ]
        )

        output = CsvFormatter().format(result)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        assert reader.fieldnames == [
            "layer",
            "severity",
            "column",
            "message",
            "evidence",
            "explanation",
        ]
        assert len(rows) == 4
        assert rows[0]["severity"] == "critical"
        assert rows[0]["column"] == "customer_id"
        assert rows[0]["layer"] == "statistical"

    def test_evidence_cell_round_trips_via_json(self):
        result = ComparisonResult(
            anomalies=[
                _anomaly("warning", column="revenue", value_a=0.021, value_b=0.148, delta=0.127),
            ]
        )

        output = CsvFormatter().format(result)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        evidence = json.loads(rows[0]["evidence"])
        assert evidence == {"value_a": 0.021, "value_b": 0.148, "delta": 0.127}

    def test_explanation_field_included_when_present(self):
        anomaly = Anomaly(
            layer="schema",
            severity="critical",
            column="id",
            message="type mismatch",
            evidence={},
            explanation="This likely indicates an upstream schema change.",
        )
        result = ComparisonResult(anomalies=[anomaly])

        output = CsvFormatter().format(result)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        assert rows[0]["explanation"] == "This likely indicates an upstream schema change."

    def test_zero_anomalies_returns_header_only_valid_csv(self):
        result = ComparisonResult(anomalies=[])

        output = CsvFormatter().format(result)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        assert reader.fieldnames == [
            "layer",
            "severity",
            "column",
            "message",
            "evidence",
            "explanation",
        ]
        assert rows == []
