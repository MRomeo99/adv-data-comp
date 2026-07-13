import pytest
from pydantic import ValidationError

from adv_data_comp.models import (
    Anomaly,
    ComparisonMeta,
    ComparisonResult,
    FileMeta,
    Severity,
)


class TestAnomaly:
    def test_creates_a_valid_anomaly_with_required_fields(self):
        anomaly = Anomaly(
            layer="statistical",
            severity="warning",
            column="revenue",
            message="null rate increased from 2.1% to 14.8%",
        )

        assert anomaly.layer == "statistical"
        assert anomaly.severity == Severity.WARNING
        assert anomaly.column == "revenue"
        assert anomaly.explanation is None

    def test_rejects_an_invalid_severity(self):
        with pytest.raises(ValidationError):
            Anomaly(
                layer="statistical",
                severity="catastrophic",
                column="revenue",
                message="x",
            )

    def test_accepts_arbitrary_evidence_payload(self):
        anomaly = Anomaly(
            layer="statistical",
            severity="critical",
            column="revenue",
            message="null rate increased",
            evidence={"value_a": 0.021, "value_b": 0.148, "delta": 0.127},
        )

        assert anomaly.evidence["delta"] == 0.127


class TestComparisonResult:
    def _anomaly(self, severity: str) -> Anomaly:
        return Anomaly(layer="schema", severity=severity, column="id", message="x")

    def test_summary_counts_anomalies_by_severity(self):
        result = ComparisonResult(
            anomalies=[
                self._anomaly("critical"),
                self._anomaly("critical"),
                self._anomaly("warning"),
                self._anomaly("info"),
                self._anomaly("suggestion"),
            ]
        )

        assert result.summary == {
            "critical": 2,
            "warning": 1,
            "info": 1,
            "suggestion": 1,
        }

    def test_summary_defaults_missing_severities_to_zero(self):
        result = ComparisonResult(anomalies=[self._anomaly("info")])

        assert result.summary == {
            "critical": 0,
            "warning": 0,
            "info": 1,
            "suggestion": 0,
        }

    def test_critical_property_filters_to_critical_only(self):
        critical = self._anomaly("critical")
        result = ComparisonResult(anomalies=[critical, self._anomaly("warning")])

        assert result.critical == [critical]

    def test_has_critical_is_true_when_any_critical_anomaly_exists(self):
        result = ComparisonResult(anomalies=[self._anomaly("critical")])
        assert result.has_critical is True

    def test_has_critical_is_false_with_no_critical_anomalies(self):
        result = ComparisonResult(anomalies=[self._anomaly("warning"), self._anomaly("info")])
        assert result.has_critical is False

    def test_schema_match_is_true_when_no_schema_anomalies(self):
        result = ComparisonResult(anomalies=[self._anomaly("critical")])
        # the one anomaly above has layer="schema", so schema_match should be False
        assert result.schema_match is False

    def test_schema_match_is_true_when_only_non_schema_anomalies(self):
        non_schema = Anomaly(layer="statistical", severity="warning", column="id", message="x")
        result = ComparisonResult(anomalies=[non_schema])
        assert result.schema_match is True

    def test_meta_is_optional_and_defaults_to_none(self):
        result = ComparisonResult(anomalies=[])
        assert result.meta is None

    def test_to_json_writes_a_file_and_returns_the_content(self, tmp_path):
        result = ComparisonResult(anomalies=[self._anomaly("critical")])
        out = tmp_path / "out.json"

        content = result.to_json(str(out))

        assert out.exists()
        assert out.read_text() == content
        assert "critical" in content

    def test_to_html_writes_a_file(self, tmp_path):
        result = ComparisonResult(anomalies=[self._anomaly("warning")])
        out = tmp_path / "out.html"

        result.to_html(str(out))

        assert out.exists()
        assert "<html" in out.read_text().lower() or "<!doctype" in out.read_text().lower()

    def test_to_dbt_yaml_writes_a_file(self, tmp_path):
        result = ComparisonResult(anomalies=[])
        out = tmp_path / "schema.yml"

        result.to_dbt_yaml(str(out))

        assert out.exists()
        assert "models" in out.read_text()

    def test_meta_can_be_attached(self):
        meta = ComparisonMeta(
            comparison_id="abc-123",
            file_a=FileMeta(path="a.parquet", format="parquet", rows=100, size_mb=1.2),
            file_b=FileMeta(path="b.csv", format="csv", rows=98, size_mb=1.1),
            engine="polars",
            layers_run=["format", "schema"],
            runtime_seconds=0.5,
        )
        result = ComparisonResult(anomalies=[], meta=meta)
        assert result.meta.engine == "polars"
        assert result.meta.file_a.rows == 100
