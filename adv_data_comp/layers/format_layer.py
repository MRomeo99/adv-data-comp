from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.engine.duckdb_engine import DuckDBFrame
from adv_data_comp.layers.base import AbstractLayer
from adv_data_comp.models import Anomaly, Layer, Severity

_SAMPLE_LIMIT = 20

_DOT_DECIMAL_RE = re.compile(r"^-?\d{1,3}(,\d{3})*\.\d+$")
_COMMA_DECIMAL_RE = re.compile(r"^-?\d{1,3}(\.\d{3})*,\d+$")

_CURRENCY_START_RE = re.compile(r"^[$€£]")
_CURRENCY_END_RE = re.compile(r"[$€£]$")
_CURRENCY_SYMBOL_RE = re.compile(r"[$€£]")

_BOOLEAN_TOKEN_SETS: list[set[str]] = [
    {"true", "false"},
    {"1", "0"},
    {"yes", "no"},
    {"y", "n"},
]

_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d %Y", "%B %d, %Y"]

_NUMERIC_FIELD_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _detect_encoding(raw: bytes) -> str:
    """Try utf-8 first; on failure look for a UTF-16 BOM; else fall back to latin-1."""
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return "utf-16"
        return "latin-1"


def _detect_line_ending(raw: bytes) -> str | None:
    if b"\r\n" in raw:
        return "CRLF"
    if b"\n" in raw:
        return "LF"
    return None


def _detect_quoting_convention(path: Path) -> str | None:
    raw = path.read_bytes()[:8192]
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    data_lines = lines[1:11] if len(lines) > 1 else []

    saw_quoted = False
    saw_unquoted_string = False
    for line in data_lines:
        for field in line.split(","):
            field = field.strip()
            if not field:
                continue
            if len(field) >= 2 and field.startswith('"') and field.endswith('"'):
                saw_quoted = True
            elif not _NUMERIC_FIELD_RE.match(field):
                saw_unquoted_string = True

    if saw_quoted and not saw_unquoted_string:
        return "quoted"
    if saw_unquoted_string and not saw_quoted:
        return "unquoted"
    return None


def _sample_values(frame: EngineFrame, column: str) -> list:
    if isinstance(frame, DuckDBFrame):
        rows = frame.con.sql(
            f'SELECT "{column}" FROM {frame.view_name} '
            f'WHERE "{column}" IS NOT NULL LIMIT {_SAMPLE_LIMIT}'
        ).fetchall()
        return [row[0] for row in rows]
    return frame[column].drop_nulls().head(_SAMPLE_LIMIT).to_list()


def _matches_all(values: list[str], pattern: re.Pattern[str]) -> bool:
    return bool(values) and all(pattern.match(v) for v in values)


def _decimal_style(values: list[str]) -> str | None:
    if _matches_all(values, _DOT_DECIMAL_RE):
        return "dot"
    if _matches_all(values, _COMMA_DECIMAL_RE):
        return "comma"
    return None


def _has_currency_symbol(value: str) -> bool:
    return bool(_CURRENCY_START_RE.match(value) or _CURRENCY_END_RE.search(value))


def _currency_style(values: list[str]) -> str | None:
    if not values:
        return None
    if all(_has_currency_symbol(v) for v in values):
        return "currency"
    if all(not _has_currency_symbol(v) for v in values):
        return "plain"
    return None


def _boolean_token_set(values: list[str]) -> set[str] | None:
    if not values:
        return None
    lowered = [v.strip().lower() for v in values]
    for token_set in _BOOLEAN_TOKEN_SETS:
        if all(v in token_set for v in lowered):
            return token_set
    return None


def _date_format(values: list[str]) -> str | None:
    if not values:
        return None
    for fmt in _DATE_FORMATS:
        try:
            for v in values:
                datetime.strptime(v, fmt)
        except ValueError:
            continue
        return fmt
    return None


class FormatLayer(AbstractLayer):
    """Layer 1 — detects file-level and column-level format issues.

    Only compares columns present with the exact same name in both files;
    fuzzy name matching is handled by the semantic layer, not here.

    File-level raw-byte checks (encoding, line endings, CSV quoting) need the
    original file paths, which the AbstractLayer.compare() signature does not
    carry. They're supplied at construction time instead, so compare() itself
    still matches the abstract interface exactly.
    """

    layer_name: ClassVar[Layer] = "format"

    def __init__(self, path_a: Path, path_b: Path) -> None:
        self.path_a = Path(path_a)
        self.path_b = Path(path_b)

    def compare(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        config: ComparisonConfig,
        column_mapping: dict[str, str] | None = None,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        anomalies.extend(self._check_encoding())
        anomalies.extend(self._check_line_endings())
        anomalies.extend(self._check_quoting())
        anomalies.extend(self._check_columns(engine, frame_a, frame_b))
        return anomalies

    def _is_csv_pair(self) -> bool:
        return self.path_a.suffix.lower() == ".csv" and self.path_b.suffix.lower() == ".csv"

    def _check_encoding(self) -> list[Anomaly]:
        if not self._is_csv_pair():
            return []
        encoding_a = _detect_encoding(self.path_a.read_bytes()[:4096])
        encoding_b = _detect_encoding(self.path_b.read_bytes()[:4096])
        if encoding_a == encoding_b:
            return []
        return [
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column="__file__",
                message=(f"File encoding differs: file A is {encoding_a}, file B is {encoding_b}"),
                evidence={"encoding_a": encoding_a, "encoding_b": encoding_b},
            )
        ]

    def _check_line_endings(self) -> list[Anomaly]:
        if not self._is_csv_pair():
            return []
        line_ending_a = _detect_line_ending(self.path_a.read_bytes())
        line_ending_b = _detect_line_ending(self.path_b.read_bytes())
        if not line_ending_a or not line_ending_b or line_ending_a == line_ending_b:
            return []
        return [
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column="__file__",
                message=(
                    f"Line endings differ: file A uses {line_ending_a}, "
                    f"file B uses {line_ending_b}"
                ),
                evidence={
                    "line_ending_a": line_ending_a,
                    "line_ending_b": line_ending_b,
                },
            )
        ]

    def _check_quoting(self) -> list[Anomaly]:
        if not self._is_csv_pair():
            return []
        quoting_a = _detect_quoting_convention(self.path_a)
        quoting_b = _detect_quoting_convention(self.path_b)
        if not quoting_a or not quoting_b or quoting_a == quoting_b:
            return []
        return [
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column="__file__",
                message="CSV quoting convention differs between files",
                evidence={"quoting_a": quoting_a, "quoting_b": quoting_b},
            )
        ]

    def _check_columns(
        self, engine: AbstractEngine, frame_a: EngineFrame, frame_b: EngineFrame
    ) -> list[Anomaly]:
        schema_a = engine.schema(frame_a)
        schema_b = engine.schema(frame_b)
        common_columns = [name for name in schema_a if name in schema_b]

        anomalies: list[Anomaly] = []
        for column in common_columns:
            if schema_a[column].category != "string" and schema_b[column].category != "string":
                continue

            samples_a = [str(v) for v in _sample_values(frame_a, column)]
            samples_b = [str(v) for v in _sample_values(frame_b, column)]
            if not samples_a or not samples_b:
                continue

            anomalies.extend(self._check_decimal_separator(column, samples_a, samples_b))
            anomalies.extend(self._check_currency_symbols(column, samples_a, samples_b))
            anomalies.extend(self._check_boolean_representations(column, samples_a, samples_b))
            anomalies.extend(self._check_date_format(column, samples_a, samples_b))

        return anomalies

    def _check_decimal_separator(
        self, column: str, samples_a: list[str], samples_b: list[str]
    ) -> list[Anomaly]:
        style_a = _decimal_style(samples_a)
        style_b = _decimal_style(samples_b)
        if not style_a or not style_b or style_a == style_b:
            return []
        return [
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column=column,
                message=(
                    f"Column '{column}': decimal separator differs between files "
                    f"('{style_a}' vs '{style_b}')"
                ),
                evidence={"example_a": samples_a[0], "example_b": samples_b[0]},
            )
        ]

    def _check_currency_symbols(
        self, column: str, samples_a: list[str], samples_b: list[str]
    ) -> list[Anomaly]:
        style_a = _currency_style(samples_a)
        style_b = _currency_style(samples_b)
        if not style_a or not style_b or style_a == style_b:
            return []

        symbol = None
        for value in [*samples_a, *samples_b]:
            match = _CURRENCY_SYMBOL_RE.search(value)
            if match:
                symbol = match.group()
                break

        return [
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column=column,
                message=(
                    f"Column '{column}': currency symbol present in one file but not the other"
                ),
                evidence={
                    "symbol": symbol,
                    "example_a": samples_a[0],
                    "example_b": samples_b[0],
                },
            )
        ]

    def _check_boolean_representations(
        self, column: str, samples_a: list[str], samples_b: list[str]
    ) -> list[Anomaly]:
        set_a = _boolean_token_set(samples_a)
        set_b = _boolean_token_set(samples_b)
        if not set_a or not set_b or set_a == set_b:
            return []
        return [
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column=column,
                message=f"Column '{column}': boolean representation differs between files",
                evidence={"tokens_a": sorted(set_a), "tokens_b": sorted(set_b)},
            )
        ]

    def _check_date_format(
        self, column: str, samples_a: list[str], samples_b: list[str]
    ) -> list[Anomaly]:
        format_a = _date_format(samples_a)
        format_b = _date_format(samples_b)
        if not format_a or not format_b or format_a == format_b:
            return []
        return [
            Anomaly(
                layer="format",
                severity=Severity.INFO,
                column=column,
                message=f"Column '{column}': date format differs between files",
                evidence={"format_a": format_a, "format_b": format_b},
            )
        ]
