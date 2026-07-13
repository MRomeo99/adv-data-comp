import uuid

import polars as pl

from adv_data_comp.comparator import Comparator
from adv_data_comp.config import ComparisonConfig


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


class TestComparator:
    def test_runs_all_five_layers_by_default_in_order(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2], "revenue": [10.0, 20.0]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2], "revenue": [10.0, 20.0]})

        result = Comparator().compare(file_a, file_b)

        assert result.meta.layers_run == [
            "format",
            "schema",
            "semantic",
            "statistical",
            "referential",
        ]

    def test_respects_a_custom_layer_list(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2], "extra": [1, 2]})

        config = ComparisonConfig(layers=["schema"])
        result = Comparator(config).compare(file_a, file_b)

        assert result.meta.layers_run == ["schema"]
        assert all(a.layer == "schema" for a in result.anomalies)

    def test_a_failing_layer_does_not_prevent_later_layers_from_running(
        self, tmp_path, monkeypatch
    ):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2, 3], "extra": [1, 2, 3]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        from adv_data_comp.layers.schema_layer import SchemaLayer

        def _boom(self, engine, frame_a, frame_b, config, column_mapping=None):
            raise RuntimeError("simulated layer failure")

        monkeypatch.setattr(SchemaLayer, "compare", _boom)

        config = ComparisonConfig(layers=["schema", "referential"], key="id")
        result = Comparator(config).compare(file_a, file_b)

        assert result.meta.layers_run == ["schema", "referential"]
        assert any(
            a.layer == "schema" and "simulated layer failure" in a.message for a in result.anomalies
        )
        assert any(a.layer == "referential" for a in result.anomalies)

    def test_severity_filter_only_returns_matching_severities(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2, 3]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        config = ComparisonConfig(layers=["referential"], key="id", severity_filter=["critical"])
        result = Comparator(config).compare(file_a, file_b)

        assert all(a.severity.value == "critical" for a in result.anomalies)

    def test_referential_layer_skips_gracefully_without_a_key(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        config = ComparisonConfig(layers=["referential"])
        result = Comparator(config).compare(file_a, file_b)

        assert len(result.anomalies) == 1
        assert result.anomalies[0].severity.value == "warning"

    def test_column_mapping_from_semantic_layer_flows_into_statistical_layer(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2, 3, 4, 5]})
        file_b = _write_csv(tmp_path, "b.csv", {"cust_id": [1, 2, 3, 4, 5000]})

        config = ComparisonConfig(layers=["semantic", "statistical"])
        result = Comparator(config).compare(file_a, file_b)

        semantic_hits = [a for a in result.anomalies if a.layer == "semantic"]
        assert len(semantic_hits) == 1

        statistical_hits = [a for a in result.anomalies if a.layer == "statistical"]
        assert any("customer_id" in a.column for a in statistical_hits)

    def test_meta_is_populated_with_file_and_engine_info(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        result = Comparator().compare(file_a, file_b)

        assert uuid.UUID(result.meta.comparison_id)
        assert result.meta.file_a.rows == 2
        assert result.meta.file_b.rows == 2
        assert result.meta.file_a.format == "csv"
        assert result.meta.engine == "polars"
        assert result.meta.runtime_seconds >= 0

    def test_selects_duckdb_engine_when_forced_by_threshold(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        config = ComparisonConfig(memory_threshold_mb=0)
        result = Comparator(config).compare(file_a, file_b)

        assert result.meta.engine == "duckdb"
