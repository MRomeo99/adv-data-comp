from pathlib import Path

from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine
from adv_data_comp.engine.selector import select_engine


def _make_file(tmp_path: Path, name: str, size_bytes: int) -> Path:
    path = tmp_path / name
    path.write_bytes(b"0" * size_bytes)
    return path


class TestSelectEngine:
    def test_selects_polars_when_combined_size_is_under_the_threshold(self, tmp_path):
        file_a = _make_file(tmp_path, "a.csv", 1_000_000)
        file_b = _make_file(tmp_path, "b.csv", 1_000_000)

        engine = select_engine(file_a, file_b, threshold_mb=500)

        assert isinstance(engine, PolarsEngine)

    def test_selects_duckdb_when_combined_size_exceeds_the_threshold(self, tmp_path):
        file_a = _make_file(tmp_path, "a.csv", 300 * 1_048_576)
        file_b = _make_file(tmp_path, "b.csv", 300 * 1_048_576)

        engine = select_engine(file_a, file_b, threshold_mb=500)

        assert isinstance(engine, DuckDBEngine)

    def test_selects_polars_when_combined_size_is_exactly_the_threshold(self, tmp_path):
        file_a = _make_file(tmp_path, "a.csv", 250 * 1_048_576)
        file_b = _make_file(tmp_path, "b.csv", 250 * 1_048_576)

        engine = select_engine(file_a, file_b, threshold_mb=500)

        assert isinstance(engine, PolarsEngine)

    def test_zero_threshold_always_forces_duckdb(self, tmp_path):
        file_a = _make_file(tmp_path, "a.csv", 10)
        file_b = _make_file(tmp_path, "b.csv", 10)

        engine = select_engine(file_a, file_b, threshold_mb=0)

        assert isinstance(engine, DuckDBEngine)
