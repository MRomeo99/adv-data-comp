from __future__ import annotations

import polars as pl
import pytest

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine
from adv_data_comp.layers.schema_layer import SchemaLayer
from adv_data_comp.models import Severity

ENGINES = [PolarsEngine, DuckDBEngine]


@pytest.fixture(params=ENGINES, ids=["polars", "duckdb"])
def engine(request):
    return request.param()


@pytest.fixture
def config():
    return ComparisonConfig()


@pytest.fixture
def layer():
    return SchemaLayer()


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


def _schema_anomalies(anomalies, column=None):
    if column is None:
        return anomalies
    return [a for a in anomalies if a.column == column]


class TestNoAnomalies:
    def test_identical_schema_and_data_produce_no_anomalies(self, tmp_path, engine, config, layer):
        path_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2, 3], "name": ["a", "b", "c"]})
        path_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2, 3], "name": ["a", "b", "c"]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert anomalies == []


class TestColumnCountDifference:
    def test_small_relative_difference_is_a_warning(self, tmp_path, engine, config, layer):
        rows_a = {f"col{i}": [1, 2, 3] for i in range(10)}
        rows_b = {f"col{i}": [1, 2, 3] for i in range(9)}
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", rows_a))
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", rows_b))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        file_anomalies = _schema_anomalies(anomalies, column="__file__")
        count_anomalies = [a for a in file_anomalies if "count_a" in a.evidence]
        assert len(count_anomalies) == 1
        anomaly = count_anomalies[0]
        assert anomaly.severity == Severity.WARNING
        assert anomaly.evidence == {"count_a": 10, "count_b": 9}
        assert "10" in anomaly.message
        assert "9" in anomaly.message

    def test_large_relative_difference_is_critical(self, tmp_path, engine, config, layer):
        rows_a = {f"col{i}": [1, 2, 3] for i in range(5)}
        rows_b = {f"col{i}": [1, 2, 3] for i in range(2)}
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", rows_a))
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", rows_b))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        count_anomalies = [a for a in anomalies if a.evidence.get("count_a") == 5]
        assert len(count_anomalies) == 1
        anomaly = count_anomalies[0]
        assert anomaly.severity == Severity.CRITICAL
        assert anomaly.column == "__file__"
        assert anomaly.evidence == {"count_a": 5, "count_b": 2}

    def test_no_count_anomaly_when_counts_match(self, tmp_path, engine, config, layer):
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", {"id": [1], "name": ["a"]}))
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", {"id": [1], "name": ["a"]}))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert not any("count_a" in a.evidence for a in anomalies)


class TestMissingAndExtraColumns:
    def test_column_missing_from_file_b(self, tmp_path, engine, config, layer):
        frame_a = engine.read(
            _write_csv(tmp_path, "a.csv", {"id": [1], "name": ["a"], "age": [30]})
        )
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", {"id": [1], "name": ["a"]}))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        age_anomalies = _schema_anomalies(anomalies, column="age")
        assert len(age_anomalies) == 1
        assert age_anomalies[0].severity == Severity.WARNING
        assert "missing" in age_anomalies[0].message.lower()
        assert "b" in age_anomalies[0].message.lower()

    def test_column_added_in_file_b(self, tmp_path, engine, config, layer):
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", {"id": [1], "name": ["a"]}))
        frame_b = engine.read(
            _write_csv(tmp_path, "b.csv", {"id": [1], "name": ["a"], "extra_col": [42]})
        )

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        extra_anomalies = _schema_anomalies(anomalies, column="extra_col")
        assert len(extra_anomalies) == 1
        assert extra_anomalies[0].severity == Severity.WARNING
        message = extra_anomalies[0].message.lower()
        assert "new" in message or "added" in message


class TestColumnTypeDifferences:
    def test_type_category_change_is_critical(self, tmp_path, engine, config, layer):
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", {"id": [1, 2, 3], "amount": [1, 2, 3]}))
        frame_b = engine.read(
            _write_csv(tmp_path, "b.csv", {"id": [1, 2, 3], "amount": ["x", "y", "z"]})
        )

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        amount_anomalies = [
            a for a in _schema_anomalies(anomalies, column="amount") if "type_a" in a.evidence
        ]
        assert len(amount_anomalies) == 1
        anomaly = amount_anomalies[0]
        assert anomaly.severity == Severity.CRITICAL
        assert anomaly.message == "Column type: int -> string"
        assert anomaly.evidence["type_a"]
        assert anomaly.evidence["type_b"]

    def test_no_type_anomaly_when_categories_match(self, tmp_path, engine, config, layer):
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", {"amount": [1, 2, 3]}))
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", {"amount": [4, 5, 6]}))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert not any("type_a" in a.evidence for a in anomalies)


class TestNullabilityDifferences:
    def test_moderate_null_rate_shift_is_a_warning(self, tmp_path, engine, config, layer):
        frame_a = engine.read(
            _write_csv(
                tmp_path,
                "a.csv",
                {"revenue": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]},
            )
        )
        frame_b = engine.read(
            _write_csv(
                tmp_path,
                "b.csv",
                {"revenue": [1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]},
            )
        )

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        null_anomalies = [
            a for a in _schema_anomalies(anomalies, column="revenue") if "null_rate_a" in a.evidence
        ]
        assert len(null_anomalies) == 1
        anomaly = null_anomalies[0]
        assert anomaly.severity == Severity.WARNING
        assert anomaly.evidence["null_rate_a"] == pytest.approx(0.0)
        assert anomaly.evidence["null_rate_b"] == pytest.approx(0.1)
        assert anomaly.evidence["delta"] == pytest.approx(0.1)

    def test_large_null_rate_shift_is_critical(self, tmp_path, engine, config, layer):
        frame_a = engine.read(
            _write_csv(
                tmp_path,
                "a.csv",
                {"revenue": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]},
            )
        )
        frame_b = engine.read(
            _write_csv(
                tmp_path,
                "b.csv",
                {"revenue": [None, None, None, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]},
            )
        )

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        null_anomalies = [
            a for a in _schema_anomalies(anomalies, column="revenue") if "null_rate_a" in a.evidence
        ]
        assert len(null_anomalies) == 1
        anomaly = null_anomalies[0]
        assert anomaly.severity == Severity.CRITICAL
        assert anomaly.evidence["delta"] == pytest.approx(0.3)

    def test_no_null_anomaly_for_small_shift(self, tmp_path, engine, config, layer):
        frame_a = engine.read(
            _write_csv(
                tmp_path,
                "a.csv",
                {"revenue": [1.0] * 100},
            )
        )
        frame_b = engine.read(
            _write_csv(
                tmp_path,
                "b.csv",
                {"revenue": [1.0] * 98 + [None, None]},
            )
        )

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert not any("null_rate_a" in a.evidence for a in anomalies)


class TestColumnOrderDifferences:
    def test_same_columns_different_order_emits_single_info_anomaly(
        self, tmp_path, engine, config, layer
    ):
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", {"a": [1], "b": [2], "c": [3]}))
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", {"c": [3], "a": [1], "b": [2]}))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        order_anomalies = [a for a in anomalies if "order_a" in a.evidence]
        assert len(order_anomalies) == 1
        anomaly = order_anomalies[0]
        assert anomaly.severity == Severity.INFO
        assert anomaly.column == "__file__"
        assert anomaly.message == "Column order differs"
        assert anomaly.evidence["order_a"] == ["a", "b", "c"]
        assert anomaly.evidence["order_b"] == ["c", "a", "b"]

    def test_no_order_anomaly_when_column_sets_differ(self, tmp_path, engine, config, layer):
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", {"a": [1], "b": [2], "c": [3]}))
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", {"a": [1], "b": [2], "d": [4]}))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert not any("order_a" in a.evidence for a in anomalies)

    def test_no_order_anomaly_when_order_matches(self, tmp_path, engine, config, layer):
        frame_a = engine.read(_write_csv(tmp_path, "a.csv", {"a": [1], "b": [2], "c": [3]}))
        frame_b = engine.read(_write_csv(tmp_path, "b.csv", {"a": [1], "b": [2], "c": [3]}))

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert not any("order_a" in a.evidence for a in anomalies)
