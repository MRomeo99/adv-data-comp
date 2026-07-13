"""Typer CLI entry point for adv-data-comp.

See CLAUDE.md's "CLI interface — full specification" section for the exact
command/option contract this module implements.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Optional

import typer
import yaml

# Windows terminals often default stdout/stderr to a legacy codec (cp1252)
# that can't encode the emoji/box-drawing characters Rich renders; force
# UTF-8 so `compare` doesn't crash on a plain cmd.exe/PowerShell session.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from rich.console import Console
from rich.table import Table

from adv_data_comp import __version__
from adv_data_comp.comparator import Comparator
from adv_data_comp.config import ComparisonConfig, OutputFormat
from adv_data_comp.engine.base import AbstractEngine
from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine
from adv_data_comp.formatters.terminal import TerminalFormatter

app = typer.Typer(help="Universal data file comparison and anomaly detection.")

# Fixed ordering used to turn "--severity" (a MINIMUM severity) into the
# severity_filter list ComparisonConfig expects: passing "warning" means
# "show critical and warning" (see CLAUDE.md CLI spec: "Filter anomalies by
# minimum severity").
_SEVERITY_ORDER = ["critical", "warning", "info", "suggestion"]

# Per-format report filenames written under --output-dir. "dbt" gets
# schema.yml (matching dbt's own convention) rather than report.dbt/.yaml,
# since that's the file dbt projects actually expect.
_REPORT_FILENAMES = {
    "html": "report.html",
    "json": "report.json",
    "yaml": "report.yaml",
    "markdown": "report.md",
    "csv": "report.csv",
    "dbt": "schema.yml",
}
_REPORT_METHODS = {
    "html": "to_html",
    "json": "to_json",
    "yaml": "to_yaml",
    "markdown": "to_markdown",
    "csv": "to_csv",
    "dbt": "to_dbt_yaml",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Single source of truth for hardcoded defaults, so the CLI's fallback
# values can never drift from ComparisonConfig's own defaults.
_CONFIG_DEFAULTS = ComparisonConfig()


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _severity_filter_from_minimum(minimum: str | None) -> list[str] | None:
    """Converts a single "minimum severity" into the ordered list of
    severities at or above it (critical > warning > info > suggestion)."""
    if minimum is None:
        return None
    if minimum not in _SEVERITY_ORDER:
        raise typer.BadParameter(
            f"Invalid --severity value: {minimum!r}. Must be one of {_SEVERITY_ORDER}."
        )
    idx = _SEVERITY_ORDER.index(minimum)
    return _SEVERITY_ORDER[: idx + 1]


def _load_config_file(path: Optional[Path]) -> dict[str, Any]:
    if path is None:
        return {}
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return raw or {}


def _pick(cli_value: Any, file_dict: dict[str, Any], file_key: str, default: Any) -> Any:
    """Merge precedence: CLI flag (if explicitly given) > config file value
    (if present) > hardcoded default. `cli_value=None` means "not passed on
    the command line" for every option below (list/bool options use `None`
    sentinels rather than their "empty" value for exactly this reason)."""
    if cli_value is not None:
        return cli_value
    file_value = file_dict.get(file_key)
    if file_value is not None:
        return file_value
    return default


def _select_single_engine(path: Path, memory_threshold_mb: float) -> AbstractEngine:
    size_mb = path.stat().st_size / 1_048_576
    if size_mb <= memory_threshold_mb:
        return PolarsEngine()
    return DuckDBEngine()


@app.command()
def compare(
    file_a: str = typer.Argument(..., help='Path to first file (the "expected" or "reference" file).'),
    file_b: str = typer.Argument(..., help='Path to second file (the "actual" or "new" file).'),
    key: Optional[str] = typer.Option(None, "--key", help="Key column for row-level comparison."),
    layers: Optional[str] = typer.Option(
        None, "--layers", help="Comma-separated layers to run [default: all]."
    ),
    report: list[str] = typer.Option(
        [], "--report", help="Output format(s): html, json, yaml, markdown, csv, dbt. Repeatable."
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="Directory for report files [default: ./]."
    ),
    explain: Optional[bool] = typer.Option(
        None,
        "--explain/--no-explain",
        help="Enable LLM anomaly explanations (requires Portkey).",
    ),
    severity: Optional[str] = typer.Option(
        None,
        "--severity",
        help="Filter anomalies by minimum severity: critical, warning, info, suggestion.",
    ),
    fuzzy_threshold: Optional[float] = typer.Option(
        None, "--fuzzy-threshold", help="Fuzzy column match threshold [default: 0.80]."
    ),
    memory_threshold_mb: Optional[float] = typer.Option(
        None, "--memory-threshold-mb", help="Engine switch threshold [default: 500]."
    ),
    sheet: Optional[str] = typer.Option(None, "--sheet", help="Excel sheet name [default: first sheet]."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable Rich terminal formatting."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress terminal output (for CI use)."),
    fail_on_critical: Optional[bool] = typer.Option(
        None,
        "--fail-on-critical/--no-fail-on-critical",
        help="Exit code 1 if any critical anomalies found.",
    ),
    config: Optional[Path] = typer.Option(None, "--config", help="Load options from YAML config file."),
) -> None:
    """Compare FILE_A (reference) against FILE_B (new/actual) across the five layers."""
    file_dict = _load_config_file(config)

    cli_layers = [item.strip() for item in layers.split(",")] if layers else None
    cli_report = report if report else None

    merged_key = _pick(key, file_dict, "key", _CONFIG_DEFAULTS.key)
    merged_layers = _pick(cli_layers, file_dict, "layers", list(_CONFIG_DEFAULTS.layers))
    merged_report = _pick(cli_report, file_dict, "report", [])
    merged_output_dir = _pick(output_dir, file_dict, "output_dir", _CONFIG_DEFAULTS.output_dir)
    merged_explain = _pick(explain, file_dict, "explain", _CONFIG_DEFAULTS.explain)
    merged_severity = _pick(severity, file_dict, "severity", None)
    merged_fuzzy_threshold = _pick(
        fuzzy_threshold, file_dict, "fuzzy_threshold", _CONFIG_DEFAULTS.fuzzy_threshold
    )
    merged_memory_threshold_mb = _pick(
        memory_threshold_mb, file_dict, "memory_threshold_mb", _CONFIG_DEFAULTS.memory_threshold_mb
    )
    merged_sheet = _pick(sheet, file_dict, "sheet", _CONFIG_DEFAULTS.sheet)
    merged_fail_on_critical = _pick(fail_on_critical, file_dict, "fail_on_critical", False)

    comparison_config = ComparisonConfig(
        key=merged_key,
        layers=merged_layers,
        fuzzy_threshold=merged_fuzzy_threshold,
        memory_threshold_mb=merged_memory_threshold_mb,
        explain=merged_explain,
        output_dir=merged_output_dir,
        severity_filter=_severity_filter_from_minimum(merged_severity),
        sheet=merged_sheet,
    )

    result = Comparator(comparison_config).compare(file_a, file_b)

    if not quiet:
        text = TerminalFormatter().format(result)
        if no_color:
            text = _strip_ansi(text)
        typer.echo(text, nl=False)

    if merged_report:
        output_dir_path = Path(merged_output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)
        for fmt in merged_report:
            fmt_key = fmt.lower()
            if fmt_key not in _REPORT_FILENAMES:
                raise typer.BadParameter(f"Unsupported --report format: {fmt!r}")
            target = str(output_dir_path / _REPORT_FILENAMES[fmt_key])
            getattr(result, _REPORT_METHODS[fmt_key])(target)

    if merged_fail_on_critical and result.has_critical:
        raise typer.Exit(code=1)


@app.command()
def profile(
    file: str = typer.Argument(..., help="Path to the file to profile."),
    memory_threshold_mb: float = typer.Option(
        _CONFIG_DEFAULTS.memory_threshold_mb, "--memory-threshold-mb", help="Engine switch threshold."
    ),
) -> None:
    """Profile a single file (no comparison): column name, dtype, null rate, distinct count."""
    path = Path(file)
    engine = _select_single_engine(path, memory_threshold_mb)
    frame = engine.read(path)
    schema_map = engine.schema(frame)

    table = Table(title=f"Profile: {path}")
    table.add_column("Column")
    table.add_column("Dtype")
    table.add_column("Null Rate")
    table.add_column("Distinct Count")

    for name in schema_map:
        col_profile = engine.profile_column(frame, name)
        table.add_row(
            name,
            schema_map[name].raw,
            f"{col_profile.null_rate:.2%}",
            str(col_profile.distinct_count),
        )

    Console().print(table)


@app.command()
def schema(
    file: str = typer.Argument(..., help="Path to the file to inspect."),
    memory_threshold_mb: float = typer.Option(
        _CONFIG_DEFAULTS.memory_threshold_mb, "--memory-threshold-mb", help="Engine switch threshold."
    ),
) -> None:
    """Print the inferred schema (column name + normalized category) for a single file."""
    path = Path(file)
    engine = _select_single_engine(path, memory_threshold_mb)
    frame = engine.read(path)
    schema_map = engine.schema(frame)

    table = Table(title=f"Schema: {path}")
    table.add_column("Column")
    table.add_column("Category")
    for name, column_type in schema_map.items():
        table.add_row(name, column_type.category)

    Console().print(table)


@app.command()
def formats() -> None:
    """List supported output formats."""
    for output_format in OutputFormat:
        typer.echo(output_format.value)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
