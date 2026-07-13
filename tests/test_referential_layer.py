from __future__ import annotations

import polars as pl
import pytest

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine
from adv_data_comp.layers.referential_layer import ReferentialLayer

ENGINES = [PolarsEngine, DuckDBEngine]


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


@pytest.fixture(params=ENGINES, ids=["polars", "duckdb"])
def engine(request):
    return request.param()


class TestNoKey:
    def test_returns_single_warning_when_key_is_none(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2]})
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 2]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key=None)

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        assert len(anomalies) == 1
        anomaly = anomalies[0]
        assert anomaly.severity == "warning"
        assert anomaly.column == "__file__"
        assert anomaly.layer == "referential"
        assert "skip" in anomaly.message.lower()

    def test_returns_single_warning_when_key_is_empty_string(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2]})
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 2]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        assert len(anomalies) == 1
        assert anomalies[0].column == "__file__"


class TestMissingRowsInB:
    def test_warns_when_missing_ratio_is_5_percent_or_less(self, tmp_path, engine):
        # 20 rows in A, B is missing exactly 1 key (5% -> not > 5%, so warning)
        path_a = _write_csv(
            tmp_path, "a.csv", {"customer_id": list(range(1, 21)), "name": [f"n{i}" for i in range(1, 21)]}
        )
        path_b = _write_csv(
            tmp_path, "b.csv", {"customer_id": list(range(2, 21)), "name": [f"n{i}" for i in range(2, 21)]}
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        missing = [a for a in anomalies if "not found in file B" in a.message]
        assert len(missing) == 1
        assert missing[0].severity == "warning"
        assert missing[0].column == "customer_id"
        assert missing[0].evidence["missing_count"] == 1

    def test_critical_when_missing_ratio_exceeds_5_percent(self, tmp_path, engine):
        # 20 rows in A, B missing 3 keys (15% -> critical)
        path_a = _write_csv(
            tmp_path, "a.csv", {"customer_id": list(range(1, 21)), "name": [f"n{i}" for i in range(1, 21)]}
        )
        path_b = _write_csv(
            tmp_path, "b.csv", {"customer_id": list(range(4, 21)), "name": [f"n{i}" for i in range(4, 21)]}
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        missing = [a for a in anomalies if "not found in file B" in a.message]
        assert len(missing) == 1
        assert missing[0].severity == "critical"
        assert missing[0].evidence["missing_count"] == 3

    def test_no_anomaly_when_all_a_keys_present_in_b(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2, 3], "name": ["a", "b", "c"]})
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 2, 3], "name": ["a", "b", "c"]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        missing = [a for a in anomalies if "not found in file B" in a.message]
        assert len(missing) == 0


class TestNewRowsInB:
    def test_warns_on_new_rows_in_b_not_in_a(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2, 3], "name": ["a", "b", "c"]})
        path_b = _write_csv(
            tmp_path, "b.csv", {"customer_id": [1, 2, 3, 4, 5], "name": ["a", "b", "c", "d", "e"]}
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        new_rows = [a for a in anomalies if a.evidence.get("new_count") is not None]
        assert len(new_rows) == 1
        assert new_rows[0].severity == "warning"
        assert new_rows[0].column == "customer_id"
        assert new_rows[0].evidence["new_count"] == 2

    def test_new_rows_are_always_warning_even_if_ratio_is_large(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1], "name": ["a"]})
        path_b = _write_csv(
            tmp_path, "b.csv", {"customer_id": [1, 2, 3, 4, 5, 6], "name": list("abcdef")}
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        new_rows = [a for a in anomalies if a.evidence.get("new_count") is not None]
        assert len(new_rows) == 1
        assert new_rows[0].severity == "warning"
        assert new_rows[0].evidence["new_count"] == 5


class TestDuplicateKeys:
    def test_flags_duplicates_in_file_a(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 1, 2], "name": ["a", "a2", "b"]})
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 2], "name": ["a", "b"]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        dup = [a for a in anomalies if a.evidence.get("file") == "a" and "duplicate_count" in a.evidence]
        assert len(dup) == 1
        assert dup[0].severity == "critical"
        assert dup[0].evidence["duplicate_count"] == 1

    def test_flags_duplicates_in_file_b(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2], "name": ["a", "b"]})
        path_b = _write_csv(
            tmp_path, "b.csv", {"customer_id": [1, 2, 2, 2], "name": ["a", "b", "b2", "b3"]}
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        dup = [a for a in anomalies if a.evidence.get("file") == "b" and "duplicate_count" in a.evidence]
        assert len(dup) == 1
        assert dup[0].severity == "critical"
        assert dup[0].evidence["duplicate_count"] == 2

    def test_no_duplicate_anomaly_when_keys_are_unique(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2, 3], "name": ["a", "b", "c"]})
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 2, 3], "name": ["a", "b", "c"]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        dup = [a for a in anomalies if "duplicate_count" in a.evidence]
        assert len(dup) == 0


class TestKeyFormatConsistency:
    def test_warns_when_key_formats_are_disjoint(self, tmp_path, engine):
        path_a = _write_csv(
            tmp_path,
            "a.csv",
            {"customer_id": ["CUST-0001", "CUST-0002", "CUST-0003"], "name": ["a", "b", "c"]},
        )
        path_b = _write_csv(
            tmp_path, "b.csv", {"customer_id": ["A1001", "A1002", "A1003"], "name": ["a", "b", "c"]}
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        fmt = [a for a in anomalies if "format" in a.message.lower()]
        assert len(fmt) == 1
        assert fmt[0].severity == "warning"
        assert fmt[0].column == "customer_id"

    def test_no_format_anomaly_when_key_formats_match(self, tmp_path, engine):
        path_a = _write_csv(
            tmp_path,
            "a.csv",
            {"customer_id": ["CUST-0001", "CUST-0002", "CUST-0003"], "name": ["a", "b", "c"]},
        )
        path_b = _write_csv(
            tmp_path,
            "b.csv",
            {"customer_id": ["CUST-0001", "CUST-0002", "CUST-0004"], "name": ["a", "b", "d"]},
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        fmt = [a for a in anomalies if "format" in a.message.lower()]
        assert len(fmt) == 0


class TestValueDifferencesForMatchedRows:
    def test_flags_column_with_mismatches_on_matched_keys(self, tmp_path, engine):
        path_a = _write_csv(
            tmp_path,
            "a.csv",
            {"customer_id": [1, 2, 3], "name": ["alice", "bob", "carol"], "revenue": [100.0, 200.0, 300.0]},
        )
        path_b = _write_csv(
            tmp_path,
            "b.csv",
            {"customer_id": [1, 2, 3], "name": ["alice", "bobby", "carol"], "revenue": [100.0, 200.0, 999.0]},
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        name_diff = [a for a in anomalies if a.column == "name" and "differing_row_count" in a.evidence]
        revenue_diff = [a for a in anomalies if a.column == "revenue" and "differing_row_count" in a.evidence]

        assert len(name_diff) == 1
        assert name_diff[0].severity == "warning"
        assert name_diff[0].evidence["differing_row_count"] == 1
        assert name_diff[0].evidence["key"] == "customer_id"
        assert 2 in name_diff[0].evidence["example_keys"]

        assert len(revenue_diff) == 1
        assert revenue_diff[0].evidence["differing_row_count"] == 1
        assert 3 in revenue_diff[0].evidence["example_keys"]

    def test_treats_null_vs_null_as_equal_not_a_mismatch(self, tmp_path, engine):
        path_a = _write_csv(
            tmp_path,
            "a.csv",
            {"customer_id": [1, 2], "notes": [None, "hello"]},
        )
        path_b = _write_csv(
            tmp_path,
            "b.csv",
            {"customer_id": [1, 2], "notes": [None, "hello"]},
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        notes_diff = [a for a in anomalies if a.column == "notes" and "differing_row_count" in a.evidence]
        assert len(notes_diff) == 0

    def test_no_value_diff_anomaly_when_all_matched_rows_are_identical(self, tmp_path, engine):
        path_a = _write_csv(
            tmp_path, "a.csv", {"customer_id": [1, 2, 3], "name": ["a", "b", "c"]}
        )
        path_b = _write_csv(
            tmp_path, "b.csv", {"customer_id": [1, 2, 3], "name": ["a", "b", "c"]}
        )
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        config = ComparisonConfig(key="customer_id")

        anomalies = ReferentialLayer().compare(engine, frame_a, frame_b, config)

        value_diffs = [a for a in anomalies if "differing_row_count" in a.evidence]
        assert len(value_diffs) == 0
