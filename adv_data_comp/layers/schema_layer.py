from __future__ import annotations

from typing import ClassVar

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.layers.base import AbstractLayer
from adv_data_comp.models import Anomaly, Layer, Severity

# Column count difference: critical if the count differs by more than this
# fraction of the larger file's column count, otherwise a warning.
_COUNT_DIFF_CRITICAL_RATIO = 0.20

# Nullability difference: only flagged once the null-rate delta exceeds this
# threshold; critical above the second (larger) threshold.
_NULL_RATE_DELTA_WARNING = 0.05
_NULL_RATE_DELTA_CRITICAL = 0.20


class SchemaLayer(AbstractLayer):
    """Layer 2 — compares column structure between two files."""

    layer_name: ClassVar[Layer] = "schema"

    def compare(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        config: ComparisonConfig,
        column_mapping: dict[str, str] | None = None,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        schema_a = engine.schema(frame_a)
        schema_b = engine.schema(frame_b)
        names_a = list(schema_a.keys())
        names_b = list(schema_b.keys())
        set_a = set(names_a)
        set_b = set(names_b)

        anomalies.extend(self._check_column_count(schema_a, schema_b))
        anomalies.extend(self._check_missing_and_extra_columns(names_a, names_b, set_a, set_b))
        anomalies.extend(self._check_type_differences(schema_a, schema_b, set_a, set_b, names_a))
        anomalies.extend(self._check_nullability(engine, frame_a, frame_b, set_a, set_b, names_a))
        anomalies.extend(self._check_column_order(names_a, names_b, set_a, set_b))

        return anomalies

    def _check_column_count(self, schema_a, schema_b) -> list[Anomaly]:
        count_a, count_b = len(schema_a), len(schema_b)
        if count_a == count_b:
            return []

        larger = max(count_a, count_b)
        diff = abs(count_a - count_b)
        relative_diff = diff / larger if larger else 0.0
        severity = (
            Severity.CRITICAL if relative_diff > _COUNT_DIFF_CRITICAL_RATIO else Severity.WARNING
        )

        return [
            Anomaly(
                layer="schema",
                severity=severity,
                column="__file__",
                message=(
                    f"Column count differs: file A has {count_a} columns, "
                    f"file B has {count_b} columns"
                ),
                evidence={"count_a": count_a, "count_b": count_b},
            )
        ]

    def _check_missing_and_extra_columns(
        self, names_a: list[str], names_b: list[str], set_a: set[str], set_b: set[str]
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        for name in names_a:
            if name not in set_b:
                anomalies.append(
                    Anomaly(
                        layer="schema",
                        severity=Severity.WARNING,
                        column=name,
                        message=f"Column '{name}' is present in file A but missing from file B",
                        evidence={},
                    )
                )
        for name in names_b:
            if name not in set_a:
                anomalies.append(
                    Anomaly(
                        layer="schema",
                        severity=Severity.WARNING,
                        column=name,
                        message=f"Column '{name}' is new in file B (not present in file A)",
                        evidence={},
                    )
                )
        return anomalies

    def _check_type_differences(
        self, schema_a, schema_b, set_a: set[str], set_b: set[str], names_a: list[str]
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        for name in names_a:
            if name not in set_b:
                continue
            type_a = schema_a[name]
            type_b = schema_b[name]
            if type_a.category != type_b.category:
                anomalies.append(
                    Anomaly(
                        layer="schema",
                        severity=Severity.CRITICAL,
                        column=name,
                        message=f"Column type: {type_a.category} -> {type_b.category}",
                        evidence={"type_a": type_a.raw, "type_b": type_b.raw},
                    )
                )
        return anomalies

    def _check_nullability(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        set_a: set[str],
        set_b: set[str],
        names_a: list[str],
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        for name in names_a:
            if name not in set_b:
                continue
            profile_a = engine.profile_column(frame_a, name)
            profile_b = engine.profile_column(frame_b, name)
            rate_a = profile_a.null_rate
            rate_b = profile_b.null_rate
            delta = abs(rate_a - rate_b)
            if delta > _NULL_RATE_DELTA_WARNING:
                severity = (
                    Severity.CRITICAL if delta > _NULL_RATE_DELTA_CRITICAL else Severity.WARNING
                )
                anomalies.append(
                    Anomaly(
                        layer="schema",
                        severity=severity,
                        column=name,
                        message=(
                            f"Null rate for '{name}' shifted from " f"{rate_a:.1%} to {rate_b:.1%}"
                        ),
                        evidence={
                            "null_rate_a": rate_a,
                            "null_rate_b": rate_b,
                            "delta": delta,
                        },
                    )
                )
        return anomalies

    def _check_column_order(
        self, names_a: list[str], names_b: list[str], set_a: set[str], set_b: set[str]
    ) -> list[Anomaly]:
        if set_a != set_b:
            return []
        if names_a == names_b:
            return []
        return [
            Anomaly(
                layer="schema",
                severity=Severity.INFO,
                column="__file__",
                message="Column order differs",
                evidence={"order_a": names_a, "order_b": names_b},
            )
        ]
