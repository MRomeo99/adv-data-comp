from __future__ import annotations

from pathlib import Path

import polars as pl

from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.models import ColumnProfile, ColumnType

_CATEGORY_BY_POLARS_BASE = {
    "Int8": "int",
    "Int16": "int",
    "Int32": "int",
    "Int64": "int",
    "UInt8": "int",
    "UInt16": "int",
    "UInt32": "int",
    "UInt64": "int",
    "Float32": "float",
    "Float64": "float",
    "Boolean": "bool",
    "Date": "date",
    "Datetime": "datetime",
    "Utf8": "string",
    "String": "string",
}


def _categorize_polars_dtype(dtype: pl.DataType) -> str:
    return _CATEGORY_BY_POLARS_BASE.get(dtype.base_type().__name__, "other")


class PolarsEngine(AbstractEngine):
    """In-memory engine for small/medium files (combined size <= threshold)."""

    def read(self, path: Path) -> EngineFrame:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pl.read_csv(path)
        if suffix == ".parquet":
            return pl.read_parquet(path)
        raise ValueError(f"Unsupported file format: {suffix}")

    def schema(self, frame: pl.DataFrame) -> dict[str, ColumnType]:
        return {
            name: ColumnType(raw=str(dtype), category=_categorize_polars_dtype(dtype))
            for name, dtype in frame.schema.items()
        }

    def profile_column(self, frame: pl.DataFrame, column: str) -> ColumnProfile:
        series = frame[column]
        row_count = frame.height
        null_count = series.null_count()
        is_numeric = series.dtype.is_numeric()

        min_value = series.min() if row_count > 0 else None
        max_value = series.max() if row_count > 0 else None
        raw_mean = series.mean() if is_numeric and row_count > 0 else None
        raw_stddev = series.std() if is_numeric and row_count > 1 else None
        mean = float(raw_mean) if raw_mean is not None else None
        stddev = float(raw_stddev) if raw_stddev is not None else None

        return ColumnProfile(
            name=column,
            dtype=str(series.dtype),
            null_count=null_count,
            row_count=row_count,
            distinct_count=series.n_unique(),
            min_value=min_value,
            max_value=max_value,
            mean=mean,
            stddev=stddev,
        )

    def row_count(self, frame: pl.DataFrame) -> int:
        return frame.height

    def find_missing_keys(
        self, frame_a: pl.DataFrame, frame_b: pl.DataFrame, key: str
    ) -> pl.DataFrame:
        return frame_a.filter(~pl.col(key).is_in(frame_b[key].implode()))
