from __future__ import annotations

import polars as pl

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.engine.duckdb_engine import DuckDBFrame
from adv_data_comp.layers.base import AbstractLayer
from adv_data_comp.models import Anomaly, Severity

_MAX_FORMAT_SAMPLE = 20
_MAX_EXAMPLE_KEYS = 3


def _shape(value: object) -> str:
    """Reduce a value to a "shape" by replacing digits with D and letters with L."""
    text = str(value)
    chars = []
    for ch in text:
        if ch.isdigit():
            chars.append("D")
        elif ch.isalpha():
            chars.append("L")
        else:
            chars.append(ch)
    return "".join(chars)


class ReferentialLayer(AbstractLayer):
    """Layer 5 — compares rows across files using a key column."""

    layer_name = "referential"

    def compare(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        config: ComparisonConfig,
        column_mapping: dict[str, str] | None = None,
    ) -> list[Anomaly]:
        if not config.key:
            return [
                Anomaly(
                    layer="referential",
                    severity=Severity.WARNING,
                    column="__file__",
                    message="Referential layer skipped: no --key provided",
                    evidence={},
                )
            ]

        key = config.key
        anomalies: list[Anomaly] = []

        anomalies.extend(self._missing_rows(engine, frame_a, frame_b, key))
        anomalies.extend(self._new_rows(engine, frame_a, frame_b, key))
        anomalies.extend(self._duplicate_keys(engine, frame_a, frame_b, key))
        anomalies.extend(self._format_consistency(engine, frame_a, frame_b, key))
        anomalies.extend(self._value_differences(engine, frame_a, frame_b, key))

        return anomalies

    def _missing_rows(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        key: str,
    ) -> list[Anomaly]:
        missing = engine.find_missing_keys(frame_a, frame_b, key)
        count = engine.row_count(missing)
        if count == 0:
            return []

        row_count_a = engine.row_count(frame_a)
        ratio = count / row_count_a if row_count_a else 0.0
        severity = Severity.CRITICAL if ratio > 0.05 else Severity.WARNING

        return [
            Anomaly(
                layer="referential",
                severity=severity,
                column=key,
                message=f"{count} rows in file A not found in file B",
                evidence={"missing_count": count},
            )
        ]

    def _new_rows(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        key: str,
    ) -> list[Anomaly]:
        new = engine.find_missing_keys(frame_b, frame_a, key)
        count = engine.row_count(new)
        if count == 0:
            return []

        return [
            Anomaly(
                layer="referential",
                severity=Severity.WARNING,
                column=key,
                message=f"{count} new rows in file B not found in file A",
                evidence={"new_count": count},
            )
        ]

    def _duplicate_keys(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        key: str,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        for label, frame in (("a", frame_a), ("b", frame_b)):
            profile = engine.profile_column(frame, key)
            if profile.distinct_count is None:
                continue
            duplicate_count = profile.row_count - profile.distinct_count
            if duplicate_count > 0:
                anomalies.append(
                    Anomaly(
                        layer="referential",
                        severity=Severity.CRITICAL,
                        column=key,
                        message=(
                            f"{duplicate_count} duplicate key values found in file "
                            f"{label.upper()}; row-level comparison may be unreliable"
                        ),
                        evidence={"file": label, "duplicate_count": duplicate_count},
                    )
                )
        return anomalies

    def _sample_key_values(self, frame: EngineFrame, key: str) -> list[object]:
        if isinstance(frame, DuckDBFrame):
            rows = frame.con.sql(
                f'SELECT "{key}" FROM {frame.view_name} WHERE "{key}" IS NOT NULL '
                f"LIMIT {_MAX_FORMAT_SAMPLE}"
            ).fetchall()
            return [row[0] for row in rows]
        return frame[key].drop_nulls().head(_MAX_FORMAT_SAMPLE).to_list()

    def _format_consistency(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        key: str,
    ) -> list[Anomaly]:
        samples_a = self._sample_key_values(frame_a, key)
        samples_b = self._sample_key_values(frame_b, key)

        if not samples_a or not samples_b:
            return []

        shapes_a = {_shape(v) for v in samples_a}
        shapes_b = {_shape(v) for v in samples_b}

        if shapes_a.isdisjoint(shapes_b):
            return [
                Anomaly(
                    layer="referential",
                    severity=Severity.WARNING,
                    column=key,
                    message="Key value formats are inconsistent between file A and file B",
                    evidence={
                        "example_a": samples_a[:3],
                        "example_b": samples_b[:3],
                        "shapes_a": sorted(shapes_a),
                        "shapes_b": sorted(shapes_b),
                    },
                )
            ]
        return []

    def _shared_non_key_columns(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        key: str,
    ) -> list[str]:
        columns_a = list(engine.schema(frame_a).keys())
        columns_b = set(engine.schema(frame_b).keys())
        return [c for c in columns_a if c != key and c in columns_b]

    def _value_differences(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        key: str,
    ) -> list[Anomaly]:
        columns = self._shared_non_key_columns(engine, frame_a, frame_b, key)
        if not columns:
            return []

        if isinstance(frame_a, DuckDBFrame):
            return self._value_differences_duckdb(frame_a, frame_b, key, columns)
        return self._value_differences_polars(frame_a, frame_b, key, columns)

    def _value_differences_duckdb(
        self, frame_a: DuckDBFrame, frame_b: DuckDBFrame, key: str, columns: list[str]
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        con = frame_a.con
        for column in columns:
            rows = con.sql(
                f'SELECT a."{key}" FROM {frame_a.view_name} a '
                f'JOIN {frame_b.view_name} b ON a."{key}" = b."{key}" '
                f'WHERE a."{column}" IS DISTINCT FROM b."{column}"'
            ).fetchall()
            count = len(rows)
            if count == 0:
                continue
            example_keys = [row[0] for row in rows[:_MAX_EXAMPLE_KEYS]]
            anomalies.append(
                Anomaly(
                    layer="referential",
                    severity=Severity.WARNING,
                    column=column,
                    message=f"{count} matched rows differ in column '{column}'",
                    evidence={
                        "key": key,
                        "differing_row_count": count,
                        "example_keys": example_keys,
                    },
                )
            )
        return anomalies

    def _value_differences_polars(
        self, frame_a: pl.DataFrame, frame_b: pl.DataFrame, key: str, columns: list[str]
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        joined = frame_a.join(frame_b, on=key, how="inner", suffix="_b")

        for column in columns:
            other_column = f"{column}_b"
            if other_column not in joined.columns:
                continue

            mismatch_expr = pl.col(column).ne_missing(pl.col(other_column))
            mismatches = joined.filter(mismatch_expr)
            count = mismatches.height
            if count == 0:
                continue

            example_keys = mismatches[key].head(_MAX_EXAMPLE_KEYS).to_list()
            anomalies.append(
                Anomaly(
                    layer="referential",
                    severity=Severity.WARNING,
                    column=column,
                    message=f"{count} matched rows differ in column '{column}'",
                    evidence={
                        "key": key,
                        "differing_row_count": count,
                        "example_keys": example_keys,
                    },
                )
            )
        return anomalies
