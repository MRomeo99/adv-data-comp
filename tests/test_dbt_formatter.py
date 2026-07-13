from __future__ import annotations

import yaml

from adv_data_comp.formatters.dbt_formatter import DbtFormatter
from adv_data_comp.models import (
    Anomaly,
    ComparisonMeta,
    ComparisonResult,
    FileMeta,
    Severity,
)


def _meta(file_a_path: str = "customers.parquet") -> ComparisonMeta:
    return ComparisonMeta(
        comparison_id="test-uuid-1234",
        file_a=FileMeta(path=file_a_path, format="parquet", rows=50000, size_mb=45.0),
        file_b=FileMeta(path="customers_b.csv", format="csv", rows=47153, size_mb=38.0),
        engine="duckdb",
        layers_run=["schema", "statistical", "referential"],
        runtime_seconds=4.2,
    )


def _find_column(parsed: dict, name: str) -> dict:
    model = parsed["models"][0]
    for col in model["columns"]:
        if col["name"] == name:
            return col
    raise AssertionError(f"column {name!r} not found in {model['columns']!r}")


def test_null_rate_increase_produces_not_null_test():
    anomalies = [
        Anomaly(
            layer="statistical",
            severity=Severity.WARNING,
            column="revenue",
            message="Null rate differs: 2.1% (A) vs 14.8% (B)",
            evidence={"null_rate_a": 0.021, "null_rate_b": 0.148, "delta": 0.127},
        )
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    output = DbtFormatter().format(result)
    parsed = yaml.safe_load(output)

    revenue = _find_column(parsed, "revenue")
    assert "not_null" in revenue["tests"]


def test_null_rate_not_increased_does_not_produce_not_null_test():
    # File A has a HIGHER null rate than B -- this should not trigger a
    # not_null guard, since the direction the rule cares about (B
    # introducing more nulls than the reference file A) doesn't hold.
    anomalies = [
        Anomaly(
            layer="statistical",
            severity=Severity.WARNING,
            column="revenue",
            message="Null rate differs: 14.8% (A) vs 2.1% (B)",
            evidence={"null_rate_a": 0.148, "null_rate_b": 0.021, "delta": 0.127},
        )
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    output = DbtFormatter().format(result)
    parsed = yaml.safe_load(output)

    model = parsed["models"][0]
    assert model["columns"] == []


def test_duplicate_key_in_file_a_produces_unique_test():
    anomalies = [
        Anomaly(
            layer="referential",
            severity=Severity.CRITICAL,
            column="customer_id",
            message="4 duplicate key values found in file A; row-level comparison may be unreliable",
            evidence={"file": "a", "duplicate_count": 4},
        )
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    output = DbtFormatter().format(result)
    parsed = yaml.safe_load(output)

    customer_id = _find_column(parsed, "customer_id")
    assert "unique" in customer_id["tests"]


def test_duplicate_key_in_file_b_does_not_produce_unique_test():
    # Rule 2 is scoped to duplicates found in file A (the reference/ground
    # truth file) specifically -- duplicates only in B don't imply the
    # reference data is unique, so no dbt `unique` test should be added.
    anomalies = [
        Anomaly(
            layer="referential",
            severity=Severity.CRITICAL,
            column="customer_id",
            message="4 duplicate key values found in file B; row-level comparison may be unreliable",
            evidence={"file": "b", "duplicate_count": 4},
        )
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    output = DbtFormatter().format(result)
    parsed = yaml.safe_load(output)

    model = parsed["models"][0]
    assert model["columns"] == []


def test_missing_rows_referential_anomaly_is_skipped():
    # "Rows in A not in B" doesn't map cleanly to a single-file dbt test
    # (a `relationships` test needs a parent model reference this tool
    # doesn't have) -- it must be skipped rather than fabricated.
    anomalies = [
        Anomaly(
            layer="referential",
            severity=Severity.CRITICAL,
            column="customer_id",
            message="3847 rows in file A not found in file B",
            evidence={"missing_count": 3847},
        )
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    output = DbtFormatter().format(result)
    parsed = yaml.safe_load(output)

    model = parsed["models"][0]
    assert model["columns"] == []


def test_anomaly_types_with_no_dbt_mapping_produce_no_spurious_tests():
    anomalies = [
        Anomaly(
            layer="format",
            severity=Severity.INFO,
            column="signup_date",
            message="Date format: MM/DD/YYYY -> YYYY-MM-DD",
            evidence={"format_a": "MM/DD/YYYY", "format_b": "YYYY-MM-DD"},
        ),
        Anomaly(
            layer="semantic",
            severity=Severity.SUGGESTION,
            column="cust_email",
            message="Possible fuzzy match with customer_email",
            evidence={"similarity_score": 0.91},
        ),
        Anomaly(
            layer="schema",
            severity=Severity.CRITICAL,
            column="customer_id",
            message="Column type: int64 -> string",
            evidence={"type_a": "int64", "type_b": "string"},
        ),
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    output = DbtFormatter().format(result)
    parsed = yaml.safe_load(output)

    model = parsed["models"][0]
    assert model["columns"] == []


def test_zero_anomalies_produces_valid_yaml_without_crashing():
    result = ComparisonResult(anomalies=[], meta=_meta())

    output = DbtFormatter().format(result)
    parsed = yaml.safe_load(output)

    assert parsed["models"][0]["name"] == "customers"
    assert parsed["models"][0]["columns"] == []


def test_model_name_derived_from_file_a_stem():
    result = ComparisonResult(anomalies=[], meta=_meta(file_a_path="/data/customers.parquet"))

    parsed = yaml.safe_load(DbtFormatter().format(result))

    assert parsed["models"][0]["name"] == "customers"


def test_model_name_placeholder_when_meta_is_none():
    result = ComparisonResult(anomalies=[], meta=None)

    parsed = yaml.safe_load(DbtFormatter().format(result))

    assert parsed["models"][0]["name"] == "compared_model"
    assert parsed["models"][0]["columns"] == []


def test_multiple_tests_for_same_column_are_grouped_not_duplicated():
    anomalies = [
        Anomaly(
            layer="statistical",
            severity=Severity.WARNING,
            column="customer_id",
            message="Null rate differs: 0.0% (A) vs 5.0% (B)",
            evidence={"null_rate_a": 0.0, "null_rate_b": 0.05, "delta": 0.05},
        ),
        Anomaly(
            layer="referential",
            severity=Severity.CRITICAL,
            column="customer_id",
            message="2 duplicate key values found in file A",
            evidence={"file": "a", "duplicate_count": 2},
        ),
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    parsed = yaml.safe_load(DbtFormatter().format(result))
    model = parsed["models"][0]

    matching = [c for c in model["columns"] if c["name"] == "customer_id"]
    assert len(matching) == 1
    assert sorted(matching[0]["tests"]) == ["not_null", "unique"]


def test_output_is_valid_yaml_string():
    anomalies = [
        Anomaly(
            layer="statistical",
            severity=Severity.WARNING,
            column="revenue",
            message="Null rate differs: 2.1% (A) vs 14.8% (B)",
            evidence={"null_rate_a": 0.021, "null_rate_b": 0.148, "delta": 0.127},
        )
    ]
    result = ComparisonResult(anomalies=anomalies, meta=_meta())

    output = DbtFormatter().format(result)

    assert isinstance(output, str)
    parsed = yaml.safe_load(output)
    assert "models" in parsed
    assert parsed["models"][0]["columns"][0]["name"] == "revenue"
    assert parsed["models"][0]["columns"][0]["tests"] == ["not_null"]
