import polars as pl
import pytest

from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine

ENGINES = [PolarsEngine, DuckDBEngine]


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


@pytest.fixture(params=ENGINES, ids=["polars", "duckdb"])
def engine(request):
    return request.param()


class TestRead:
    def test_read_returns_a_frame_matching_the_csv_contents(self, tmp_path, engine):
        path = _write_csv(
            tmp_path,
            "customers.csv",
            {"customer_id": [1, 2, 3], "revenue": [100.0, 200.0, None]},
        )

        frame = engine.read(path)

        assert engine.row_count(frame) == 3

    def test_read_supports_parquet(self, tmp_path, engine):
        path = tmp_path / "customers.parquet"
        pl.DataFrame({"customer_id": [1, 2], "revenue": [100.0, 200.0]}).write_parquet(path)

        frame = engine.read(path)

        assert engine.row_count(frame) == 2

    def test_read_raises_a_clear_error_for_unsupported_formats(self, tmp_path, engine):
        path = tmp_path / "customers.txt"
        path.write_text("not a supported format")

        with pytest.raises(ValueError, match="Unsupported file format"):
            engine.read(path)


class TestProfileColumn:
    def test_computes_null_count_and_distinct_count(self, tmp_path, engine):
        path = _write_csv(
            tmp_path,
            "customers.csv",
            {"customer_id": [1, 2, 2, 3], "revenue": [100.0, None, 200.0, 300.0]},
        )
        frame = engine.read(path)

        profile = engine.profile_column(frame, "revenue")

        assert profile.name == "revenue"
        assert profile.row_count == 4
        assert profile.null_count == 1
        assert profile.null_rate == pytest.approx(0.25)

    def test_computes_distinct_count_for_a_column_with_duplicates(self, tmp_path, engine):
        path = _write_csv(tmp_path, "customers.csv", {"customer_id": [1, 2, 2, 3]})
        frame = engine.read(path)

        profile = engine.profile_column(frame, "customer_id")

        assert profile.distinct_count == 3

    def test_computes_mean_and_stddev_for_numeric_columns(self, tmp_path, engine):
        path = _write_csv(tmp_path, "customers.csv", {"revenue": [10.0, 20.0, 30.0]})
        frame = engine.read(path)

        profile = engine.profile_column(frame, "revenue")

        assert profile.mean == pytest.approx(20.0)
        assert profile.stddev == pytest.approx(10.0)
        assert profile.min_value == pytest.approx(10.0)
        assert profile.max_value == pytest.approx(30.0)

    def test_leaves_mean_and_stddev_none_for_string_columns(self, tmp_path, engine):
        path = _write_csv(tmp_path, "customers.csv", {"name": ["alice", "bob", "carol"]})
        frame = engine.read(path)

        profile = engine.profile_column(frame, "name")

        assert profile.mean is None
        assert profile.stddev is None
        assert profile.min_value == "alice"
        assert profile.max_value == "carol"


class TestFindMissingKeys:
    def test_returns_rows_in_a_whose_key_is_absent_from_b(self, tmp_path, engine):
        path_a = _write_csv(
            tmp_path,
            "a.csv",
            {"customer_id": [1, 2, 3], "name": ["alice", "bob", "carol"]},
        )
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 3], "name": ["alice", "carol"]})

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        missing = engine.find_missing_keys(frame_a, frame_b, "customer_id")

        assert engine.row_count(missing) == 1

    def test_returns_no_rows_when_all_keys_are_present_in_b(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2]})
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 2, 3]})

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        missing = engine.find_missing_keys(frame_a, frame_b, "customer_id")

        assert engine.row_count(missing) == 0
