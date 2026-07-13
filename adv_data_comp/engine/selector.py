from __future__ import annotations

from pathlib import Path

from adv_data_comp.engine.base import AbstractEngine
from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine


def select_engine(file_a: Path, file_b: Path, threshold_mb: float) -> AbstractEngine:
    """Selects Polars or DuckDB based on combined file size.

    The caller never needs to know which engine ran.
    """
    total_mb = (file_a.stat().st_size + file_b.stat().st_size) / 1_048_576
    if total_mb <= threshold_mb:
        return PolarsEngine()
    return DuckDBEngine()
