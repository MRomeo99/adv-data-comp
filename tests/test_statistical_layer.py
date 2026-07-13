from __future__ import annotations

import datetime

import polars as pl
import pytest

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine
from adv_data_comp.layers.statistical_layer import StatisticalLayer
from adv_data_comp.models import Severity

ENGINES = [PolarsEngine, DuckDBEngine]


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


def _write_parquet(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_parquet(path)
    return path


@pytest.fixture(params=ENGINES, ids=["polars", "duckdb"])
def engine(request):
    return request.param()


def _compare(engine, tmp_path, rows_a, rows_b, column_mapping=None):
    path_a = _write_csv(tmp_path, "a.csv", rows_a)
    path_b = _write_csv(tmp_path, "b.csv", rows_b)
    frame_a = engine.read(path_a)
    frame_b = engine.read(path_b)
    layer = StatisticalLayer()
    return layer.compare(engine, frame_a, frame_b, ComparisonConfig(), column_mapping)


class TestRowCountCheck:
    def test_no_anomaly_when_row_counts_match(self, tmp_path, engine):
        anomalies = _compare(
            engine,
            tmp_path,
            {"revenue": [1.0, 2.0, 3.0]},
            {"revenue": [1.0, 2.0, 3.0]},
        )
        assert not any(a.column == "__file__" for a in anomalies)

    def test_warning_when_row_count_differs_by_more_than_one_percent(self, tmp_path, engine):
        rows_a = {"revenue": [float(i) for i in range(200)]}
        rows_b = {"revenue": [float(i) for i in range(195)]}  # 2.5% fewer rows
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        row_anomalies = [a for a in anomalies if a.column == "__file__"]
        assert len(row_anomalies) == 1
        assert row_anomalies[0].severity == Severity.WARNING
        assert row_anomalies[0].evidence["rows_a"] == 200
        assert row_anomalies[0].evidence["rows_b"] == 195

    def test_critical_when_row_count_differs_by_more_than_ten_percent(self, tmp_path, engine):
        rows_a = {"revenue": [float(i) for i in range(100)]}
        rows_b = {"revenue": [float(i) for i in range(85)]}  # 15% fewer rows
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        row_anomalies = [a for a in anomalies if a.column == "__file__"]
        assert len(row_anomalies) == 1
        assert row_anomalies[0].severity == Severity.CRITICAL


class TestNullRateCheck:
    def test_no_anomaly_when_null_rates_are_close(self, tmp_path, engine):
        rows_a = {"revenue": [1.0, 2.0, None, 4.0] * 25}
        rows_b = {"revenue": [1.0, 2.0, None, 4.0] * 25}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        assert not [a for a in anomalies if "Null rate" in a.message]

    def test_warning_when_null_rate_delta_exceeds_five_percent(self, tmp_path, engine):
        rows_a = {"revenue": [1.0] * 90 + [None] * 10}  # 10% null
        rows_b = {"revenue": [1.0] * 74 + [None] * 26}  # 26% null -> delta 16%
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        null_anomalies = [a for a in anomalies if "Null rate" in a.message]
        assert len(null_anomalies) == 1
        assert null_anomalies[0].severity == Severity.WARNING

    def test_critical_when_null_rate_delta_exceeds_twenty_percent(self, tmp_path, engine):
        rows_a = {"revenue": [1.0] * 100}  # 0% null
        rows_b = {"revenue": [1.0] * 70 + [None] * 30}  # 30% null -> delta 30%
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        null_anomalies = [a for a in anomalies if "Null rate" in a.message]
        assert len(null_anomalies) == 1
        assert null_anomalies[0].severity == Severity.CRITICAL


class TestMinMaxCheck:
    def test_info_anomaly_when_min_or_max_differs(self, tmp_path, engine):
        rows_a = {"revenue": [10.0, 20.0, 30.0]}
        rows_b = {"revenue": [10.0, 20.0, 99.0]}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        range_anomalies = [a for a in anomalies if "range shifted" in a.message.lower()]
        assert len(range_anomalies) == 1
        assert range_anomalies[0].severity == Severity.INFO
        assert range_anomalies[0].evidence["max_a"] == pytest.approx(30.0)
        assert range_anomalies[0].evidence["max_b"] == pytest.approx(99.0)

    def test_no_anomaly_when_min_and_max_match(self, tmp_path, engine):
        rows_a = {"revenue": [10.0, 20.0, 30.0]}
        rows_b = {"revenue": [10.0, 25.0, 30.0]}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        range_anomalies = [a for a in anomalies if "range shifted" in a.message.lower()]
        assert not range_anomalies


class TestMeanCheck:
    def test_warning_when_mean_shifts_beyond_two_stddev(self, tmp_path, engine):
        # a: mean 20, stddev ~8.16 (10,20,30) -> 2*stddev ~16.3
        rows_a = {"revenue": [10.0, 20.0, 30.0] * 10}
        rows_b = {"revenue": [80.0, 90.0, 100.0] * 10}  # mean 90, far beyond 2*stddev
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        mean_anomalies = [a for a in anomalies if "Mean shifted" in a.message]
        assert len(mean_anomalies) == 1
        assert mean_anomalies[0].severity == Severity.WARNING

    def test_no_anomaly_when_mean_shift_within_two_stddev(self, tmp_path, engine):
        rows_a = {"revenue": [10.0, 20.0, 30.0] * 10}
        rows_b = {"revenue": [11.0, 21.0, 31.0] * 10}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        mean_anomalies = [a for a in anomalies if "Mean shifted" in a.message]
        assert not mean_anomalies


class TestStddevCheck:
    def test_warning_when_stddev_changes_by_more_than_fifty_percent(self, tmp_path, engine):
        rows_a = {"revenue": [10.0, 20.0, 30.0] * 10}  # stddev ~8.16
        rows_b = {"revenue": [1.0, 20.0, 39.0] * 10}  # much larger spread
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        stddev_anomalies = [a for a in anomalies if "distribution shift" in a.message]
        assert len(stddev_anomalies) == 1
        assert stddev_anomalies[0].severity == Severity.WARNING


class TestOutlierAndZeroRate:
    def test_outlier_helper_equivalent_across_engines(self, tmp_path, engine):
        # 19 tightly-clustered values + 1 extreme value; enough points that the
        # single outlier doesn't inflate stddev enough to mask itself (the
        # "masking effect" — a real risk with very small samples).
        path = _write_csv(tmp_path, "a.csv", {"x": [10.0] * 19 + [1000.0]})
        frame = engine.read(path)
        profile = engine.profile_column(frame, "x")

        count = StatisticalLayer._outlier_count(frame, "x", profile.mean, profile.stddev)

        assert count == 1

    def test_zero_count_helper_equivalent_across_engines(self, tmp_path, engine):
        path = _write_csv(tmp_path, "a.csv", {"x": [0.0, 1.0, 0.0, 3.0]})
        frame = engine.read(path)

        count = StatisticalLayer._zero_count(frame, "x")

        assert count == 2

    def test_info_anomaly_when_outlier_rate_differs(self, tmp_path, engine):
        # file B: a small fraction (5%) of extreme values relative to a tight
        # main cluster, chosen so the outliers don't inflate stddev enough to
        # mask themselves (the "masking effect" — a real risk when the
        # outlier fraction is large relative to the sample).
        rows_a = {"x": [10.0] * 100}
        rows_b = {"x": [10.0] * 95 + [500.0] * 5}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        outlier_anomalies = [a for a in anomalies if "Outlier rate" in a.message]
        assert len(outlier_anomalies) == 1
        assert outlier_anomalies[0].severity == Severity.INFO

    def test_info_anomaly_when_zero_rate_differs(self, tmp_path, engine):
        rows_a = {"x": [1.0] * 100}
        rows_b = {"x": [0.0] * 30 + [1.0] * 70}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        zero_anomalies = [a for a in anomalies if "Zero-value rate" in a.message]
        assert len(zero_anomalies) == 1
        assert zero_anomalies[0].severity == Severity.INFO


class TestStringChecks:
    def test_info_anomaly_when_distinct_count_differs(self, tmp_path, engine):
        rows_a = {"name": [f"user_{i}" for i in range(100)]}
        rows_b = {"name": ["user_0"] * 50 + [f"user_{i}" for i in range(50)]}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        distinct_anomalies = [a for a in anomalies if "Distinct value count" in a.message]
        assert len(distinct_anomalies) == 1
        assert distinct_anomalies[0].severity == Severity.INFO

    def test_avg_string_length_helper_equivalent_across_engines(self, tmp_path, engine):
        path = _write_csv(tmp_path, "a.csv", {"name": ["a", "bb", "ccc"]})
        frame = engine.read(path)

        avg_len = StatisticalLayer._avg_string_length(frame, "name")

        assert avg_len == pytest.approx(2.0)

    def test_info_anomaly_when_avg_string_length_differs(self, tmp_path, engine):
        rows_a = {"name": ["ab"] * 100}
        rows_b = {"name": ["abcdefghij"] * 100}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        len_anomalies = [a for a in anomalies if "Average string length" in a.message]
        assert len(len_anomalies) == 1
        assert len_anomalies[0].severity == Severity.INFO

    def test_info_anomaly_when_empty_string_rate_exceeds_threshold(self, tmp_path, engine):
        # Uses Parquet rather than CSV: DuckDB's CSV reader collapses a quoted
        # empty string ("") to NULL by default, same as an unquoted empty
        # field, so it can't distinguish "" from null via CSV round-trip.
        # Parquet preserves the distinction exactly on both engines.
        path_a = _write_parquet(tmp_path, "a.parquet", {"name": ["alice"] * 100})
        path_b = _write_parquet(tmp_path, "b.parquet", {"name": [""] * 5 + ["alice"] * 95})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = StatisticalLayer()

        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        empty_anomalies = [a for a in anomalies if "Empty-string rate" in a.message]
        assert len(empty_anomalies) == 1
        assert empty_anomalies[0].severity == Severity.INFO
        assert empty_anomalies[0].evidence["empty_count_b"] == 5

    def test_top_values_helper_equivalent_across_engines(self, tmp_path, engine):
        path = _write_csv(
            tmp_path, "a.csv", {"name": ["a", "a", "a", "b", "b", "c"]}
        )
        frame = engine.read(path)

        top = StatisticalLayer._top_values(frame, "name", limit=2)

        assert set(top) == {"a", "b"}

    def test_warning_anomaly_when_top10_values_shift(self, tmp_path, engine):
        rows_a = {"name": ["alpha"] * 20 + ["beta"] * 15 + ["gamma"] * 10}
        rows_b = {"name": ["delta"] * 20 + ["epsilon"] * 15 + ["zeta"] * 10}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        shift_anomalies = [a for a in anomalies if "Top-10" in a.message]
        assert len(shift_anomalies) == 1
        assert shift_anomalies[0].severity == Severity.WARNING


class TestDateChecks:
    def test_info_anomaly_when_date_range_shifts(self, tmp_path, engine):
        rows_a = {"signup_date": ["2024-01-01", "2024-01-15", "2024-01-31"]}
        rows_b = {"signup_date": ["2024-01-01", "2024-01-15", "2024-01-28"]}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        range_anomalies = [a for a in anomalies if "Date range shifted" in a.message]
        assert len(range_anomalies) == 1
        assert range_anomalies[0].severity == Severity.INFO

    def test_warning_anomaly_when_gap_detected(self, tmp_path, engine):
        dates = [
            datetime.date(2024, 1, 1) + datetime.timedelta(days=i) for i in range(10)
        ]
        # introduce a large gap after the 5th date
        dates = dates[:5] + [dates[4] + datetime.timedelta(days=30)] + dates[5:9]
        rows_a = {"signup_date": [d.isoformat() for d in dates]}
        rows_b = {"signup_date": [d.isoformat() for d in dates]}
        anomalies = _compare(engine, tmp_path, rows_a, rows_b)

        gap_anomalies = [a for a in anomalies if "Gap detected" in a.message]
        assert len(gap_anomalies) >= 1
        assert gap_anomalies[0].severity == Severity.WARNING


class TestMatchedColumns:
    def test_only_compares_exact_name_matches_by_default(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"revenue": [1.0, 2.0, 3.0], "only_a": [1, 2, 3]})
        path_b = _write_csv(tmp_path, "b.csv", {"revenue": [1.0, 2.0, 3.0], "only_b": [1, 2, 3]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = StatisticalLayer()

        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        assert not any(a.column in ("only_a", "only_b") for a in anomalies)

    def test_uses_column_mapping_for_fuzzy_matches(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1.0] * 90 + [2.0] * 10})
        path_b = _write_csv(tmp_path, "b.csv", {"cust_id": [1.0] * 60 + [2.0] * 40})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = StatisticalLayer()

        anomalies_without_mapping = layer.compare(engine, frame_a, frame_b, ComparisonConfig())
        anomalies_with_mapping = layer.compare(
            engine, frame_a, frame_b, ComparisonConfig(), column_mapping={"cust_id": "customer_id"}
        )

        assert anomalies_without_mapping == []
        assert len(anomalies_with_mapping) > 0


class TestBaseline:
    def test_no_anomalies_for_identical_files(self, tmp_path, engine):
        rows = {
            "revenue": [10.0, 20.0, 30.0, 40.0] * 25,
            "name": ["alice", "bob", "carol", "dave"] * 25,
            "signup_date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"] * 25,
        }
        anomalies = _compare(engine, tmp_path, rows, dict(rows))

        assert anomalies == []
