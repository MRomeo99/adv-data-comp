from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path

import duckdb

from adv_data_comp.engine.base import AbstractEngine
from adv_data_comp.models import ColumnCategory, ColumnProfile, ColumnType

_NUMERIC_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
}

_CATEGORY_BY_DUCKDB_TYPE: dict[str, ColumnCategory] = {
    "TINYINT": "int",
    "SMALLINT": "int",
    "INTEGER": "int",
    "BIGINT": "int",
    "HUGEINT": "int",
    "UTINYINT": "int",
    "USMALLINT": "int",
    "UINTEGER": "int",
    "UBIGINT": "int",
    "FLOAT": "float",
    "DOUBLE": "float",
    "DECIMAL": "float",
    "BOOLEAN": "bool",
    "DATE": "date",
    "TIMESTAMP": "datetime",
    "VARCHAR": "string",
}


def _categorize_duckdb_dtype(raw: str) -> ColumnCategory:
    base = raw.split("(")[0].upper()
    return _CATEGORY_BY_DUCKDB_TYPE.get(base, "other")


_view_counter = itertools.count()


@dataclass
class DuckDBFrame:
    con: duckdb.DuckDBPyConnection
    view_name: str


class DuckDBEngine(AbstractEngine):
    """Streaming engine for large files (combined size > threshold), never
    loading the full file into memory."""

    def __init__(self) -> None:
        self._con = duckdb.connect(database=":memory:")

    def read(self, path: Path) -> DuckDBFrame:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            reader = f"read_csv_auto('{path.as_posix()}')"
        elif suffix == ".parquet":
            reader = f"read_parquet('{path.as_posix()}')"
        else:
            raise ValueError(f"Unsupported file format: {suffix}")

        view_name = f"frame_{next(_view_counter)}"
        self._con.sql(f"CREATE OR REPLACE VIEW {view_name} AS SELECT * FROM {reader}")
        return DuckDBFrame(con=self._con, view_name=view_name)

    def schema(self, frame: DuckDBFrame) -> dict[str, ColumnType]:
        rows = frame.con.sql(
            f"SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = '{frame.view_name}' ORDER BY ordinal_position"
        ).fetchall()
        return {
            name: ColumnType(raw=dtype, category=_categorize_duckdb_dtype(dtype))
            for name, dtype in rows
        }

    def profile_column(self, frame: DuckDBFrame, column: str) -> ColumnProfile:
        con, view = frame.con, frame.view_name

        dtype_row = con.sql(
            f"SELECT data_type FROM information_schema.columns "
            f"WHERE table_name = '{view}' AND column_name = '{column}'"
        ).fetchone()
        dtype = dtype_row[0] if dtype_row else "UNKNOWN"
        is_numeric = dtype.split("(")[0].upper() in _NUMERIC_TYPES

        count_row = con.sql(
            f'SELECT COUNT(*), COUNT(*) FILTER (WHERE "{column}" IS NULL), '
            f'COUNT(DISTINCT "{column}") FROM {view}'
        ).fetchone()
        assert count_row is not None  # COUNT(*) always returns exactly one row
        row_count, null_count, distinct_count = count_row

        min_value = max_value = None
        if row_count > 0:
            minmax_row = con.sql(f'SELECT MIN("{column}"), MAX("{column}") FROM {view}').fetchone()
            assert minmax_row is not None
            min_value, max_value = minmax_row

        mean = stddev = None
        if is_numeric and row_count > 0:
            stats_row = con.sql(
                f'SELECT AVG("{column}"), STDDEV("{column}") FROM {view}'
            ).fetchone()
            assert stats_row is not None
            mean, stddev = stats_row

        return ColumnProfile(
            name=column,
            dtype=dtype,
            null_count=null_count,
            row_count=row_count,
            distinct_count=distinct_count,
            min_value=min_value,
            max_value=max_value,
            mean=float(mean) if mean is not None else None,
            stddev=float(stddev) if stddev is not None else None,
        )

    def row_count(self, frame: DuckDBFrame) -> int:
        row = frame.con.sql(f"SELECT COUNT(*) FROM {frame.view_name}").fetchone()
        assert row is not None  # COUNT(*) always returns exactly one row
        return row[0]

    def find_missing_keys(
        self, frame_a: DuckDBFrame, frame_b: DuckDBFrame, key: str
    ) -> DuckDBFrame:
        con = frame_a.con
        view_name = f"frame_{next(_view_counter)}"
        con.sql(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f'SELECT * FROM {frame_a.view_name} WHERE "{key}" NOT IN '
            f'(SELECT "{key}" FROM {frame_b.view_name})'
        )
        return DuckDBFrame(con=con, view_name=view_name)
