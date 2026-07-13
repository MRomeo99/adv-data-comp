from __future__ import annotations

from typing import Any

import polars as pl

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.engine.duckdb_engine import DuckDBFrame
from adv_data_comp.layers.base import AbstractLayer
from adv_data_comp.models import Anomaly, ColumnProfile, Severity

_NUMERIC_CATEGORIES = {"int", "float"}
_DATE_CATEGORIES = {"date", "datetime"}

# --- Thresholds explicitly pinned down by the CLAUDE.md spec ---------------
_ROW_COUNT_WARN_PCT = 0.01
_ROW_COUNT_CRITICAL_PCT = 0.10
_NULL_RATE_WARN_DELTA = 0.05
_NULL_RATE_CRITICAL_DELTA = 0.20
_MEAN_STDDEV_MULTIPLE = 2.0
_STDDEV_REL_DELTA = 0.5
_DISTINCT_COUNT_REL_DELTA = 0.10
_EMPTY_STRING_RATE_THRESHOLD = 0.01
_TOP10_OVERLAP_THRESHOLD = 0.5

# --- Thresholds NOT pinned down by the spec — chosen here and documented ---
# Outlier-rate and zero-rate deltas: flagged as "info" once they differ by
# more than 2 percentage points between file A and file B. This mirrors the
# null-rate check's spirit (a delta-based signal) but at "info" severity
# since outlier/zero counts are secondary signals, not primary distribution
# shape statistics.
_OUTLIER_RATE_DELTA_THRESHOLD = 0.02
_ZERO_RATE_DELTA_THRESHOLD = 0.02
# Average string length: flagged as "info" when the absolute difference
# exceeds the larger of 1 character or 10% of file A's average length —
# the flat 1-char floor avoids noisy flags on already-short strings.
_AVG_STRLEN_ABS_MIN_DELTA = 1.0
_AVG_STRLEN_REL_DELTA = 0.10
# Date gap detection: a gap counts as anomalous once it is at least 2x the
# most common ("expected") cadence between consecutive distinct dates.
_DATE_GAP_MULTIPLE = 2.0


class StatisticalLayer(AbstractLayer):
    """Layer 4 — compares value distributions per matched column.

    Only runs on matched columns: exact-name matches (the intersection of
    ``engine.schema(frame_a)`` and ``engine.schema(frame_b)`` keys) plus any
    fuzzy matches supplied via ``column_mapping``.

    Note on "future date presence" (CLAUDE.md item 4c): intentionally
    omitted. Detecting "future" dates requires a live `datetime.now()` /
    `date.today()` reference, which this codebase forbids in logic covered by
    tests (it makes tests flaky/non-deterministic). A safe deterministic
    variant would need an externally supplied `reference_date`, which is out
    of scope for this slice.
    """

    layer_name = "statistical"

    def compare(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        config: ComparisonConfig,
        column_mapping: dict[str, str] | None = None,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = list(self._check_row_count(engine, frame_a, frame_b))

        schema_a = engine.schema(frame_a)
        schema_b = engine.schema(frame_b)
        pairs = self._matched_column_pairs(schema_a, schema_b, column_mapping)

        for name_a, name_b in pairs:
            category_a = schema_a[name_a].category
            category_b = schema_b[name_b].category

            if category_a in _NUMERIC_CATEGORIES and category_b in _NUMERIC_CATEGORIES:
                anomalies.extend(
                    self._check_numeric_column(engine, frame_a, frame_b, name_a, name_b)
                )
            elif category_a == "string" and category_b == "string":
                anomalies.extend(
                    self._check_string_column(engine, frame_a, frame_b, name_a, name_b)
                )
            elif category_a in _DATE_CATEGORIES and category_b in _DATE_CATEGORIES:
                anomalies.extend(
                    self._check_date_column(engine, frame_a, frame_b, name_a, name_b)
                )
            # Mismatched type families are left to the schema layer to flag.

        return anomalies

    # ------------------------------------------------------------------
    # Column matching
    # ------------------------------------------------------------------

    @staticmethod
    def _matched_column_pairs(
        schema_a: dict[str, Any],
        schema_b: dict[str, Any],
        column_mapping: dict[str, str] | None,
    ) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        exact_a_names = set()
        for name in schema_a:
            if name in schema_b:
                pairs.append((name, name))
                exact_a_names.add(name)

        if column_mapping:
            for col_b, col_a in column_mapping.items():
                if col_a in schema_a and col_b in schema_b and col_a not in exact_a_names:
                    pairs.append((col_a, col_b))

        return pairs

    @staticmethod
    def _column_label(name_a: str, name_b: str) -> str:
        return name_a if name_a == name_b else f"{name_a}~{name_b}"

    # ------------------------------------------------------------------
    # 1. Row count (once per comparison)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_row_count(engine: AbstractEngine, frame_a: EngineFrame, frame_b: EngineFrame) -> list[Anomaly]:
        rows_a = engine.row_count(frame_a)
        rows_b = engine.row_count(frame_b)
        denom = rows_a or rows_b
        if not denom:
            return []

        delta_pct = abs(rows_a - rows_b) / denom
        if delta_pct <= _ROW_COUNT_WARN_PCT:
            return []

        severity = Severity.CRITICAL if delta_pct > _ROW_COUNT_CRITICAL_PCT else Severity.WARNING
        return [
            Anomaly(
                layer="statistical",
                severity=severity,
                column="__file__",
                message=(
                    f"Row count differs by {delta_pct:.1%}: "
                    f"{rows_a} rows in file A vs {rows_b} rows in file B"
                ),
                evidence={"rows_a": rows_a, "rows_b": rows_b, "delta_pct": delta_pct},
            )
        ]

    # ------------------------------------------------------------------
    # 2. Numeric columns
    # ------------------------------------------------------------------

    def _check_numeric_column(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        name_a: str,
        name_b: str,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        profile_a = engine.profile_column(frame_a, name_a)
        profile_b = engine.profile_column(frame_b, name_b)
        label = self._column_label(name_a, name_b)

        # a. null rate difference
        null_delta = abs(profile_a.null_rate - profile_b.null_rate)
        if null_delta > _NULL_RATE_WARN_DELTA:
            severity = (
                Severity.CRITICAL if null_delta > _NULL_RATE_CRITICAL_DELTA else Severity.WARNING
            )
            anomalies.append(
                Anomaly(
                    layer="statistical",
                    severity=severity,
                    column=label,
                    message=(
                        f"Null rate differs: {profile_a.null_rate:.1%} (A) "
                        f"vs {profile_b.null_rate:.1%} (B)"
                    ),
                    evidence={
                        "null_rate_a": profile_a.null_rate,
                        "null_rate_b": profile_b.null_rate,
                        "delta": null_delta,
                    },
                )
            )

        # b. min/max range shift
        if profile_a.min_value != profile_b.min_value or profile_a.max_value != profile_b.max_value:
            anomalies.append(
                Anomaly(
                    layer="statistical",
                    severity=Severity.INFO,
                    column=label,
                    message=(
                        f"Min/max range shifted: [{profile_a.min_value}, {profile_a.max_value}] (A) "
                        f"vs [{profile_b.min_value}, {profile_b.max_value}] (B)"
                    ),
                    evidence={
                        "min_a": profile_a.min_value,
                        "max_a": profile_a.max_value,
                        "min_b": profile_b.min_value,
                        "max_b": profile_b.max_value,
                    },
                )
            )

        if profile_a.stddev:
            # c. mean difference
            if (
                profile_a.mean is not None
                and profile_b.mean is not None
                and abs(profile_a.mean - profile_b.mean) > _MEAN_STDDEV_MULTIPLE * profile_a.stddev
            ):
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.WARNING,
                        column=label,
                        message=(
                            f"Mean shifted beyond {_MEAN_STDDEV_MULTIPLE:.0f} stddev: "
                            f"{profile_a.mean} (A) vs {profile_b.mean} (B)"
                        ),
                        evidence={
                            "mean_a": profile_a.mean,
                            "mean_b": profile_b.mean,
                            "stddev_a": profile_a.stddev,
                        },
                    )
                )

            # d. stddev difference
            if (
                profile_b.stddev is not None
                and abs(profile_a.stddev - profile_b.stddev) / profile_a.stddev > _STDDEV_REL_DELTA
            ):
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.WARNING,
                        column=label,
                        message=(
                            f"Stddev changed by more than {_STDDEV_REL_DELTA:.0%} — "
                            f"indicates distribution shift ({profile_a.stddev} (A) vs {profile_b.stddev} (B))"
                        ),
                        evidence={"stddev_a": profile_a.stddev, "stddev_b": profile_b.stddev},
                    )
                )

        # e. outlier count + zero-value rate differences
        anomalies.extend(
            self._check_outlier_and_zero_rates(
                frame_a, frame_b, name_a, name_b, profile_a, profile_b, label
            )
        )

        return anomalies

    @classmethod
    def _check_outlier_and_zero_rates(
        cls,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        name_a: str,
        name_b: str,
        profile_a: ColumnProfile,
        profile_b: ColumnProfile,
        label: str,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        if profile_a.row_count and profile_b.row_count:
            outlier_a = cls._outlier_count(frame_a, name_a, profile_a.mean, profile_a.stddev)
            outlier_b = cls._outlier_count(frame_b, name_b, profile_b.mean, profile_b.stddev)
            rate_a = outlier_a / profile_a.row_count
            rate_b = outlier_b / profile_b.row_count
            if abs(rate_a - rate_b) > _OUTLIER_RATE_DELTA_THRESHOLD:
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.INFO,
                        column=label,
                        message=(
                            f"Outlier rate (beyond 3 stddev of each file's own mean) differs: "
                            f"{rate_a:.1%} (A) vs {rate_b:.1%} (B)"
                        ),
                        evidence={
                            "outlier_count_a": outlier_a,
                            "outlier_count_b": outlier_b,
                            "outlier_rate_a": rate_a,
                            "outlier_rate_b": rate_b,
                        },
                    )
                )

            zero_a = cls._zero_count(frame_a, name_a)
            zero_b = cls._zero_count(frame_b, name_b)
            zrate_a = zero_a / profile_a.row_count
            zrate_b = zero_b / profile_b.row_count
            if abs(zrate_a - zrate_b) > _ZERO_RATE_DELTA_THRESHOLD:
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.INFO,
                        column=label,
                        message=(
                            f"Zero-value rate differs: {zrate_a:.1%} (A) vs {zrate_b:.1%} (B)"
                        ),
                        evidence={
                            "zero_count_a": zero_a,
                            "zero_count_b": zero_b,
                            "zero_rate_a": zrate_a,
                            "zero_rate_b": zrate_b,
                        },
                    )
                )

        return anomalies

    @staticmethod
    def _outlier_count(frame: EngineFrame, column: str, mean: float | None, stddev: float | None) -> int:
        if not stddev or mean is None:
            return 0
        if isinstance(frame, DuckDBFrame):
            return frame.con.sql(
                f'SELECT COUNT(*) FILTER (WHERE abs("{column}" - {mean}) > {3 * stddev}) '
                f"FROM {frame.view_name}"
            ).fetchone()[0]
        return frame.select(((pl.col(column) - mean).abs() > 3 * stddev).sum()).item()

    @staticmethod
    def _zero_count(frame: EngineFrame, column: str) -> int:
        if isinstance(frame, DuckDBFrame):
            return frame.con.sql(
                f'SELECT COUNT(*) FILTER (WHERE "{column}" = 0) FROM {frame.view_name}'
            ).fetchone()[0]
        return frame.select((pl.col(column) == 0).sum()).item()

    # ------------------------------------------------------------------
    # 3. String columns
    # ------------------------------------------------------------------

    def _check_string_column(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        name_a: str,
        name_b: str,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        profile_a = engine.profile_column(frame_a, name_a)
        profile_b = engine.profile_column(frame_b, name_b)
        label = self._column_label(name_a, name_b)

        # a. distinct value count difference
        da, db = profile_a.distinct_count, profile_b.distinct_count
        if da:
            rel_delta = abs(da - (db or 0)) / da
            if rel_delta > _DISTINCT_COUNT_REL_DELTA:
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.INFO,
                        column=label,
                        message=f"Distinct value count differs: {da} (A) vs {db} (B)",
                        evidence={"distinct_a": da, "distinct_b": db, "delta_pct": rel_delta},
                    )
                )

        # b. average string length difference
        avg_a = self._avg_string_length(frame_a, name_a)
        avg_b = self._avg_string_length(frame_b, name_b)
        if avg_a is not None and avg_b is not None:
            threshold = max(_AVG_STRLEN_ABS_MIN_DELTA, _AVG_STRLEN_REL_DELTA * avg_a)
            if abs(avg_a - avg_b) > threshold:
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.INFO,
                        column=label,
                        message=f"Average string length differs: {avg_a:.2f} (A) vs {avg_b:.2f} (B)",
                        evidence={"avg_len_a": avg_a, "avg_len_b": avg_b},
                    )
                )

        # c. empty string rate vs null rate
        empty_a = self._empty_string_count(frame_a, name_a)
        empty_b = self._empty_string_count(frame_b, name_b)
        rate_a = empty_a / profile_a.row_count if profile_a.row_count else 0.0
        rate_b = empty_b / profile_b.row_count if profile_b.row_count else 0.0
        if rate_a > _EMPTY_STRING_RATE_THRESHOLD or rate_b > _EMPTY_STRING_RATE_THRESHOLD:
            anomalies.append(
                Anomaly(
                    layer="statistical",
                    severity=Severity.INFO,
                    column=label,
                    message=(
                        f"Empty-string rate: {rate_a:.1%} (A) vs {rate_b:.1%} (B) "
                        "(distinct from null rate)"
                    ),
                    evidence={
                        "empty_count_a": empty_a,
                        "empty_count_b": empty_b,
                        "empty_rate_a": rate_a,
                        "empty_rate_b": rate_b,
                        "null_count_a": profile_a.null_count,
                        "null_count_b": profile_b.null_count,
                    },
                )
            )

        # d. most frequent values (top-10) shift
        top_a = self._top_values(frame_a, name_a)
        top_b = self._top_values(frame_b, name_b)
        if top_a and top_b:
            set_a, set_b = set(top_a), set(top_b)
            union = set_a | set_b
            overlap = len(set_a & set_b) / len(union) if union else 1.0
            if overlap < _TOP10_OVERLAP_THRESHOLD:
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.WARNING,
                        column=label,
                        message=f"Top-10 most frequent values shifted (overlap {overlap:.0%})",
                        evidence={"top10_a": top_a, "top10_b": top_b, "overlap": overlap},
                    )
                )

        return anomalies

    @staticmethod
    def _avg_string_length(frame: EngineFrame, column: str) -> float | None:
        if isinstance(frame, DuckDBFrame):
            result = frame.con.sql(
                f'SELECT AVG(LENGTH("{column}")) FROM {frame.view_name}'
            ).fetchone()[0]
            return float(result) if result is not None else None
        if frame.height == 0:
            return None
        result = frame.select(pl.col(column).str.len_chars().mean()).item()
        return float(result) if result is not None else None

    @staticmethod
    def _empty_string_count(frame: EngineFrame, column: str) -> int:
        if isinstance(frame, DuckDBFrame):
            return frame.con.sql(
                f"SELECT COUNT(*) FILTER (WHERE \"{column}\" = '') FROM {frame.view_name}"
            ).fetchone()[0]
        return frame.select((pl.col(column) == "").sum()).item()

    @staticmethod
    def _top_values(frame: EngineFrame, column: str, limit: int = 10) -> list[Any]:
        if isinstance(frame, DuckDBFrame):
            rows = frame.con.sql(
                f'SELECT "{column}" AS v, COUNT(*) AS c FROM {frame.view_name} '
                f'WHERE "{column}" IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT {limit}'
            ).fetchall()
            return [r[0] for r in rows]
        if frame.height == 0:
            return []
        series = frame[column].drop_nulls()
        if series.len() == 0:
            return []
        vc = series.value_counts().sort("count", descending=True).head(limit)
        return vc[column].to_list()

    # ------------------------------------------------------------------
    # 4. Date / datetime columns
    # ------------------------------------------------------------------

    def _check_date_column(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        name_a: str,
        name_b: str,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        profile_a = engine.profile_column(frame_a, name_a)
        profile_b = engine.profile_column(frame_b, name_b)
        label = self._column_label(name_a, name_b)

        # a. date range shift
        if profile_a.min_value != profile_b.min_value or profile_a.max_value != profile_b.max_value:
            anomalies.append(
                Anomaly(
                    layer="statistical",
                    severity=Severity.INFO,
                    column=label,
                    message=(
                        f"Date range shifted: [{profile_a.min_value}, {profile_a.max_value}] (A) "
                        f"vs [{profile_b.min_value}, {profile_b.max_value}] (B)"
                    ),
                    evidence={
                        "min_a": profile_a.min_value,
                        "max_a": profile_a.max_value,
                        "min_b": profile_b.min_value,
                        "max_b": profile_b.max_value,
                    },
                )
            )

        # b. gap detection — best effort, checked independently per file since
        # each file's own date sequence has its own cadence.
        for file_label, frame, name in (("a", frame_a, name_a), ("b", frame_b, name_b)):
            for gap in self._detect_date_gaps(frame, name):
                anomalies.append(
                    Anomaly(
                        layer="statistical",
                        severity=Severity.WARNING,
                        column=label,
                        message=(
                            f"Gap detected in file {file_label}: {gap['start']} to {gap['end']} "
                            f"is {gap['multiple']:.1f}x the expected cadence ({gap['expected_delta']})"
                        ),
                        evidence={"file": file_label, **gap},
                    )
                )

        # c. future date presence — intentionally omitted, see class docstring.

        return anomalies

    @staticmethod
    def _distinct_sorted_dates(frame: EngineFrame, column: str) -> list[Any]:
        if isinstance(frame, DuckDBFrame):
            rows = frame.con.sql(
                f'SELECT DISTINCT "{column}" FROM {frame.view_name} '
                f'WHERE "{column}" IS NOT NULL ORDER BY 1'
            ).fetchall()
            return [r[0] for r in rows]
        series = frame[column].drop_nulls().unique().sort()
        return series.to_list()

    @classmethod
    def _detect_date_gaps(cls, frame: EngineFrame, column: str) -> list[dict[str, Any]]:
        dates = cls._distinct_sorted_dates(frame, column)
        if len(dates) < 3:
            return []

        deltas = [dates[i + 1] - dates[i] for i in range(len(dates) - 1)]
        counts: dict[Any, int] = {}
        for delta in deltas:
            counts[delta] = counts.get(delta, 0) + 1
        expected_delta = max(counts, key=counts.get)

        gaps: list[dict[str, Any]] = []
        for i, delta in enumerate(deltas):
            multiple = delta / expected_delta
            if multiple >= _DATE_GAP_MULTIPLE:
                gaps.append(
                    {
                        "start": dates[i],
                        "end": dates[i + 1],
                        "expected_delta": str(expected_delta),
                        "gap_delta": str(delta),
                        "multiple": float(multiple),
                    }
                )
        return gaps
