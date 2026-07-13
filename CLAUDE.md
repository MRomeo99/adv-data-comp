# CLAUDE.md — adv-data-comp

This file is the source of truth for AI assistants working on this project.
Read it fully before writing any code, creating any file, or suggesting any
change. Every architectural decision, interface contract, output format, and
quality bar is documented here.

---

## What this project is

**adv-data-comp** is a universal data file comparison and anomaly detection
tool for data engineers. It compares two data files — any combination of
Parquet, CSV, or Excel — across five semantic layers and produces structured
anomaly reports in multiple output formats.

It is deliberately different from file diff tools (byte-level, useless for
data) and from data validation tools (single-file, no comparison). It answers
the question every data engineer actually has: *"these two files are supposed
to represent the same data — what's wrong with them, and why?"*

**Three interfaces, one engine:**
```bash
# CLI
adv-data-comp compare file_a.parquet file_b.csv --key customer_id --report html

# Python API
from adv_data_comp import Comparator
result = Comparator().compare("file_a.parquet", "file_b.csv", key="customer_id")

# REST API
POST /compare
```

**File size handling — seamless, automatic:**
- Files ≤ 500MB: loaded into memory via Polars for maximum speed
- Files > 500MB: streamed through DuckDB without loading into memory
- The caller never specifies which mode — the engine detects and switches
  automatically based on file size. The output is identical regardless of mode.
- This threshold is configurable via `ADV_DATA_COMP_MEMORY_THRESHOLD_MB` env
  var or `--memory-threshold` CLI flag.

**Resume signal:** Demonstrates practical, high-value data engineering
tooling — file format handling, statistical profiling, schema inference,
fuzzy matching, DuckDB as a compute engine, multi-interface library design,
and LLM-assisted diagnostics. Every data team needs this. No existing open
source tool does all of it well.

---

## The five comparison layers

The comparison engine runs all five layers in sequence. Each layer produces
a list of `Anomaly` objects with severity, evidence, and optional LLM
explanation. Layers are independent — a failure in layer 3 does not skip
layer 4.

### Layer 1 — Format layer
Detects file-level and column-level format issues before any value comparison.
Runs first because format normalization is required for all subsequent layers.

Checks:
- File encoding (UTF-8, latin-1, UTF-16) — detected, normalized, noted
- Line endings (CRLF vs LF) — noted, normalized
- Decimal separator (`.` vs `,`) — detected per column, normalized
- Thousands separator (`,` vs `.` vs `_`) — detected, normalized
- Date format variants (`2024-01-01` vs `01/01/2024` vs `Jan 1 2024`) —
  detected per column, normalized to ISO 8601
- Currency symbols (`$`, `€`, `£`) — detected, stripped, noted
- Boolean representations (`true/false`, `1/0`, `yes/no`, `Y/N`) — detected,
  normalized
- Quoted vs unquoted strings in CSV — detected, normalized

Output: `FormatAnomaly` objects with `column`, `format_a`, `format_b`,
`normalized_to`, `severity`.

### Layer 2 — Schema layer
Compares column structure after format normalization.

Checks:
- Column count difference
- Column name exact matches and mismatches
- Column type differences (string vs int vs float vs date vs bool)
- Nullability differences (column is never null in A, has 15% nulls in B)
- Column order differences (same columns, different sequence)
- Extra columns in A not in B, extra columns in B not in A

Output: `SchemaAnomaly` objects with `column`, `type_a`, `type_b`,
`null_rate_a`, `null_rate_b`, `severity`.

### Layer 3 — Semantic layer
Finds columns that mean the same thing but are named or typed differently.
This is the layer most tools skip entirely.

Checks:
- **Fuzzy name matching:** edit distance (Levenshtein) + token similarity.
  `CustomerID`, `customer_id`, `cust_id`, `CUST_ID` → same column, different
  convention. Threshold: similarity score ≥ 0.80.
- **Type coercion candidates:** string column in A that parses cleanly as
  int/float/date in B → flag as type coercion opportunity
- **Unit difference detection:** column named `revenue_usd` in A vs `revenue`
  in B — flag as potential unit mismatch requiring manual verification
- **Duplicate semantic columns:** `full_name` in A, plus `first_name` +
  `last_name` in B — flag as structural difference with same semantic intent

Output: `SemanticAnomaly` objects with `column_a`, `column_b`,
`similarity_score`, `suggested_mapping`, `severity`.

### Layer 4 — Statistical layer
Compares value distributions per column after semantic mapping is applied.
Runs on matched columns only (exact + fuzzy matches from layers 2 and 3).

Checks per numeric column:
- Row count difference (absolute + percentage)
- Null rate difference (> 5% delta → warning, > 20% delta → critical)
- Min/max range shift
- Mean difference (> 2 stddev → warning)
- Stddev difference (> 50% change → warning — indicates distribution shift)
- Outlier count difference (values beyond 3 stddev)
- Zero value rate difference

Checks per string column:
- Distinct value count difference
- Most frequent values shift (top-10 values in A vs B)
- Average string length difference
- Empty string rate vs null rate

Checks per date column:
- Date range shift (min date, max date)
- Gap detection (missing dates in sequence)
- Future date presence

Output: `StatisticalAnomaly` objects with `column`, `stat_name`,
`value_a`, `value_b`, `delta`, `delta_pct`, `severity`.

### Layer 5 — Referential layer
Compares rows across files when a key column is specified. Requires `--key`.
Gracefully skipped if no key is provided, with a warning.

Checks:
- Rows in A not in B (missing rows)
- Rows in B not in A (new rows)
- Duplicate key values within each file
- Key value format consistency (same key, different format)
- Value differences for matched rows (same key, different column values)

Output: `ReferentialAnomaly` objects with `key_value`, `anomaly_type`,
`columns_affected`, `value_a`, `value_b`, `severity`.

---

## Anomaly severity model

Every `Anomaly` object has a `severity` field:

| Severity | Meaning | Example |
|---|---|---|
| `critical` | Data cannot be trusted without fixing this | Row count differs by > 10%, key column has nulls |
| `warning` | Likely a real issue, investigate | Null rate increased 8%, mean shifted 2.5 stddev |
| `info` | Notable difference, probably intentional | Column order differs, date format different |
| `suggestion` | Possible improvement, not an error | Fuzzy column match found, type coercion available |

Anomalies are grouped by severity in all output formats. Critical anomalies
always appear first.

---

## Output formats

All output formats are generated from the same `ComparisonResult` object.
The caller specifies which formats to generate — multiple can be specified
simultaneously.

### Terminal output (default, always shown)
Rich-formatted terminal output. Color-coded by severity. Designed to be
readable in 80 and 120 column terminals.

```
adv-data-comp ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Comparing: customers_jan.parquet → customers_feb.csv
Engine: DuckDB (file size: 1.2GB > 500MB threshold)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 CRITICAL (2)
  [Schema]      Column `customer_id` type: int64 → string
  [Referential] 3,847 rows in file A not found in file B

🟡 WARNING (4)
  [Statistical] `revenue`: null rate 2.1% → 14.8% (+12.7%)
  [Statistical] `signup_date`: max date 2024-01-31 → 2024-01-28
  [Semantic]    `cust_email` ↔ `customer_email` (similarity: 0.91)
  [Format]      `revenue`: decimal separator ',' → '.'

🔵 INFO (2)
  [Schema]      Column order differs (3 columns repositioned)
  [Format]      Date format: MM/DD/YYYY → YYYY-MM-DD

💡 SUGGESTIONS (1)
  [Semantic]    `full_name` in A may split into `first_name`+`last_name` in B

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total anomalies: 9  │  Runtime: 4.2s  │  Engine: DuckDB
```

### HTML report (`--report html`)
Self-contained single HTML file. No external dependencies. Sections:
- Summary header with file metadata and anomaly counts
- Expandable anomaly cards grouped by severity
- Side-by-side schema comparison table
- Statistical profile charts (inline SVG — no JavaScript charting library)
- Column mapping visualization (shows fuzzy matches)
- If `--explain` used: LLM explanation panels per critical/warning anomaly

### JSON (`--report json`)
Machine-readable. Suitable for programmatic use in CI/CD pipelines.
```json
{
  "comparison_id": "uuid",
  "file_a": {"path": "...", "format": "parquet", "rows": 50000, "size_mb": 45},
  "file_b": {"path": "...", "format": "csv", "rows": 47153, "size_mb": 38},
  "engine": "duckdb",
  "layers_run": ["format", "schema", "semantic", "statistical", "referential"],
  "anomalies": [
    {
      "layer": "statistical",
      "severity": "warning",
      "column": "revenue",
      "stat_name": "null_rate",
      "value_a": 0.021,
      "value_b": 0.148,
      "delta": 0.127,
      "delta_pct": 605.0,
      "explanation": null
    }
  ],
  "summary": {
    "critical": 2,
    "warning": 4,
    "info": 2,
    "suggestion": 1
  },
  "runtime_seconds": 4.2
}
```

### YAML (`--report yaml`)
Same structure as JSON but YAML. Suitable for dbt-style integration and
human-readable config-as-code workflows.

### Markdown (`--report markdown`)
GitHub-flavored markdown. Designed to be pasted into a PR comment or a
Notion/Confluence page. Uses GFM tables and emoji severity indicators.
Suitable for automated PR comments in CI.

### CSV (`--report csv`)
Flat CSV of all anomalies. One row per anomaly. Suitable for further analysis
in Excel or a BI tool.

### dbt schema YAML (`--report dbt`)
Generates a dbt `schema.yml` fragment with tests derived from the comparison.
If file A is "ground truth", generates dbt tests that would catch the anomalies
found in file B. Example output:
```yaml
models:
  - name: customers
    columns:
      - name: revenue
        tests:
          - not_null  # null rate in source was 2.1%; file B shows 14.8%
          - dbt_utils.not_accepted_values:
              values: ['']
```
This is the highest-value output format for data engineers — it converts a
one-time comparison into permanent pipeline protection.

### Multiple simultaneous outputs
```bash
adv-data-comp compare a.parquet b.csv \
  --report html \
  --report json \
  --report markdown \
  --report dbt \
  --output-dir ./comparison_results/
```
All specified formats are written to `--output-dir`. Terminal output always
shown regardless.

---

## LLM explanation feature (`--explain`, opt-in)

When `--explain` is passed, critical and warning anomalies are enriched with
a plain-English explanation and suggested remediation, generated by an LLM
via Portkey.

**What gets explained:**
- Critical and warning anomalies only (not info/suggestion)
- Batched into a single Portkey call per layer to minimize API calls
- Explanation added to the `explanation` field of each `Anomaly` object
- Appears in terminal output, HTML report, and JSON/YAML/Markdown outputs

**Example LLM explanation output:**
```
Column `revenue`: null rate increased from 2.1% to 14.8% (+12.7 percentage points)

Explanation: This null rate increase of 12.7 percentage points likely indicates
a join failure or filter change in the upstream pipeline that generates file B.
When a left join loses rows, revenue values for unmatched records become null.
Check the ETL job that produces this file for recent changes to join conditions
or WHERE clauses.

Suggested fix: Verify the upstream join produces the expected row count.
Compare the key column distribution between files to identify which customer
segments are missing revenue values.
```

**Portkey configuration:**
- Prompt saved in Portkey Prompt Library as `adv-data-comp-explain`
- Referenced by env var: `PORTKEY_PROMPT_EXPLAIN_ID=pp-...`
- Model: `gemini-2.5-flash` (fast, cheap, good at technical explanations)
- Fallback: `gpt-4o-mini`
- If `PORTKEY_PROMPT_EXPLAIN_ID` is not set and `--explain` is passed:
  warn and continue without explanations — never fail silently

**Graceful degradation:**
The tool is fully functional without `--explain`. Zero API keys required for
all five comparison layers and all output formats. `--explain` is a value-add,
not a dependency.

---

## Engine selection — seamless automatic switching

The engine selection logic lives in `engine/selector.py` and is called before
any comparison work begins.

```python
def select_engine(file_a: Path, file_b: Path, threshold_mb: float) -> Engine:
    """
    Selects Polars or DuckDB engine based on combined file size.
    The caller never needs to know which engine ran.
    """
    total_mb = (file_a.stat().st_size + file_b.stat().st_size) / 1_048_576
    if total_mb <= threshold_mb:
        return PolarsEngine()
    return DuckDBEngine()
```

Both engines implement `AbstractEngine` with identical method signatures.
All five comparison layers call engine methods — they never import Polars or
DuckDB directly. This is the abstraction that makes switching transparent.

```python
class AbstractEngine(ABC):
    @abstractmethod
    def read(self, path: Path) -> EngineFrame: ...

    @abstractmethod
    def profile_column(self, frame: EngineFrame, column: str) -> ColumnProfile: ...

    @abstractmethod
    def row_count(self, frame: EngineFrame) -> int: ...

    @abstractmethod
    def find_missing_keys(
        self, frame_a: EngineFrame, frame_b: EngineFrame, key: str
    ) -> EngineFrame: ...
    # ... etc
```

**Why Polars for small files:**
Polars is faster than DuckDB for in-memory operations on small files. Its
lazy evaluation and columnar memory layout make profiling and statistical
comparison very fast.

**Why DuckDB for large files:**
DuckDB reads Parquet, CSV, and Excel directly from disk without loading into
memory. It handles files larger than available RAM via its streaming execution.
For very large files, SQL-based aggregations (null counts, distinct counts,
min/max) are also more efficient than Polars scans.

**The threshold default (500MB):**
Chosen because a 500MB file loaded as a Polars DataFrame typically consumes
~1.5–2GB RAM (type inference, index structures). Above this, DuckDB's streaming
is more reliable across different machine specs. Document this reasoning in
the README and in ADR 002.

---

## File format support matrix

| Format | Read | Detect automatically | Notes |
|---|---|---|---|
| Parquet | ✓ | ✓ | Via file extension + magic bytes |
| CSV | ✓ | ✓ | Delimiter auto-detected (comma, semicolon, tab, pipe) |
| TSV | ✓ | ✓ | Treated as CSV with tab delimiter |
| Excel (.xlsx) | ✓ | ✓ | Sheet name configurable via `--sheet` |
| Excel (.xls) | ✓ | ✓ | Legacy format, converted via openpyxl |
| JSON Lines | ✓ | ✓ | Newline-delimited JSON |
| Compressed (.gz, .zst, .bz2) | ✓ | ✓ | Auto-decompressed |
| Delta Lake | ✓ | via flag | `--format delta` required |

Format is auto-detected from extension + magic bytes. Never require the user
to specify format unless it's ambiguous.

---

## Python API contract

The Python API must be clean enough to use in a production data pipeline.

```python
from adv_data_comp import Comparator, ComparisonConfig, OutputFormat

# Minimal usage
result = Comparator().compare("file_a.parquet", "file_b.csv")

# Full configuration
config = ComparisonConfig(
    key="customer_id",
    layers=["format", "schema", "semantic", "statistical", "referential"],
    fuzzy_threshold=0.80,
    memory_threshold_mb=500,
    explain=False,
    output_formats=[OutputFormat.JSON, OutputFormat.HTML],
    output_dir="./results/",
    severity_filter=["critical", "warning"],  # only return these severities
)
result = Comparator(config).compare("file_a.parquet", "file_b.csv")

# Programmatic access to results
print(result.summary)           # {critical: 2, warning: 4, info: 2, suggestion: 1}
print(result.anomalies)         # list[Anomaly]
print(result.critical)          # list[Anomaly] filtered to critical only
print(result.has_critical)      # bool — useful for CI gate
print(result.schema_match)      # bool — do schemas match exactly?
result.to_json("output.json")
result.to_html("report.html")
result.to_dbt_yaml("schema.yml")

# Use as a CI gate
if result.has_critical:
    sys.exit(1)
```

### CI/CD pipeline usage pattern

Document this prominently in the README — it's a key use case:

```python
# In a data pipeline quality gate
from adv_data_comp import Comparator

def validate_vendor_file(expected: str, received: str) -> None:
    result = Comparator().compare(expected, received, key="id")
    if result.has_critical:
        raise DataQualityError(
            f"Vendor file failed validation: "
            f"{result.summary['critical']} critical anomalies found.\n"
            f"{result.to_markdown()}"
        )
```

---

## CLI interface — full specification

Built with **Typer**. Generates `--help` automatically. Tab completion via
`adv-data-comp --install-completion`.

### Primary command: `compare`
```
adv-data-comp compare FILE_A FILE_B [OPTIONS]

Arguments:
  FILE_A    Path to first file (the "expected" or "reference" file)
  FILE_B    Path to second file (the "actual" or "new" file)

Options:
  --key TEXT                   Key column for row-level comparison
  --layers TEXT                Comma-separated layers to run [default: all]
  --report [html|json|yaml|markdown|csv|dbt]
                               Output format(s). Repeatable.
  --output-dir PATH            Directory for report files [default: ./]
  --explain                    Enable LLM anomaly explanations (requires Portkey)
  --severity [critical|warning|info|suggestion]
                               Filter anomalies by minimum severity
  --fuzzy-threshold FLOAT      Fuzzy column match threshold [default: 0.80]
  --memory-threshold-mb FLOAT  Engine switch threshold [default: 500]
  --sheet TEXT                 Excel sheet name [default: first sheet]
  --no-color                   Disable Rich terminal formatting
  --quiet                      Suppress terminal output (for CI use)
  --fail-on-critical           Exit code 1 if any critical anomalies found
  --config PATH                Load options from YAML config file
  --help                       Show this message and exit.
```

### Config file mode (`--config`)
All CLI options can be specified in a YAML config file for repeatable runs:
```yaml
# adv-data-comp.yml
key: customer_id
layers: [format, schema, semantic, statistical, referential]
report: [html, json, dbt]
output_dir: ./comparison_results/
explain: false
fuzzy_threshold: 0.85
fail_on_critical: true
```

```bash
adv-data-comp compare a.parquet b.csv --config adv-data-comp.yml
```

### Additional commands
```
adv-data-comp profile FILE        # profile a single file (no comparison)
adv-data-comp schema FILE         # print inferred schema
adv-data-comp formats             # list supported formats
adv-data-comp version             # print version
```

---

## REST API

FastAPI server. Start with `adv-data-comp serve` or `make serve`.

```
POST /compare
Content-Type: multipart/form-data

Fields:
  file_a: <file upload>
  file_b: <file upload>
  key: customer_id (optional)
  layers: format,schema,semantic,statistical,referential (optional)
  explain: false (optional)
  report: json (optional, default json — only json/yaml available via API)

Response: ComparisonResult as JSON
```

```
GET /health
GET /version
GET /formats          # returns supported format list
```

The REST API is useful for:
- CI/CD pipelines where the comparison runs in a Docker container
- Data quality gates in Airflow/Prefect/Dagster where uploading files via API
  is easier than shared filesystem access
- Integration into data portals or internal tooling

---

## File structure

```
adv-data-comp/
├── adv_data_comp/                # main package
│   ├── __init__.py               # exports: Comparator, ComparisonConfig, etc.
│   ├── comparator.py             # main orchestrator: runs all 5 layers
│   ├── config.py                 # ComparisonConfig + CLI config file loading
│   ├── models.py                 # Anomaly, ComparisonResult, ColumnProfile, etc.
│   ├── engine/
│   │   ├── base.py               # AbstractEngine, EngineFrame
│   │   ├── selector.py           # auto engine selection logic
│   │   ├── polars_engine.py      # Polars implementation
│   │   └── duckdb_engine.py      # DuckDB implementation
│   ├── layers/
│   │   ├── base.py               # AbstractLayer
│   │   ├── format_layer.py       # Layer 1
│   │   ├── schema_layer.py       # Layer 2
│   │   ├── semantic_layer.py     # Layer 3 (fuzzy matching)
│   │   ├── statistical_layer.py  # Layer 4
│   │   └── referential_layer.py  # Layer 5
│   ├── formatters/
│   │   ├── base.py               # AbstractFormatter
│   │   ├── terminal.py           # Rich terminal output
│   │   ├── html_formatter.py     # self-contained HTML report
│   │   ├── json_formatter.py     # JSON output
│   │   ├── yaml_formatter.py     # YAML output
│   │   ├── markdown_formatter.py # GFM markdown
│   │   ├── csv_formatter.py      # flat CSV
│   │   └── dbt_formatter.py      # dbt schema.yml
│   ├── explain/
│   │   ├── portkey_explainer.py  # LLM explanation via Portkey
│   │   └── prompts/              # direct mode fallback prompts only
│   └── serve/
│       ├── app.py                # FastAPI app
│       └── routes.py             # /compare, /health, /version
├── cli/
│   └── main.py                   # Typer CLI entry point
├── tests/
│   ├── fixtures/                 # sample files for testing
│   │   ├── customers_a.parquet
│   │   ├── customers_b.csv
│   │   ├── customers_b_large.parquet  # > 500MB, triggers DuckDB
│   │   ├── sales_excel.xlsx
│   │   └── edge_cases/           # encoding issues, mixed types, etc.
│   ├── test_engine_selection.py
│   ├── test_format_layer.py
│   ├── test_schema_layer.py
│   ├── test_semantic_layer.py
│   ├── test_statistical_layer.py
│   ├── test_referential_layer.py
│   ├── test_formatters.py
│   ├── test_comparator.py
│   ├── test_cli.py
│   └── test_api.py
├── docs/
│   └── adr/
│       ├── 001-five-layer-model.md
│       ├── 002-dual-engine-threshold.md
│       ├── 003-fuzzy-matching-approach.md
│       └── 004-dbt-output-format.md
├── .github/
│   └── workflows/
│       └── ci.yml
├── Makefile
├── pyproject.toml               # package config, ruff, black, mypy, pytest
├── .env.example
├── CLAUDE.md
└── README.md
```

---

## TDD test strategy

Every layer and every formatter must be tested against fixture files that
cover the specific anomalies it's designed to detect. Tests must fail first,
then pass after implementation.

### Fixture file matrix

| Fixture | Purpose |
|---|---|
| `same_schema_same_data.parquet / .csv` | baseline: zero anomalies expected |
| `same_schema_diff_values.parquet / .csv` | statistical layer: value drift |
| `diff_schema_same_data.parquet / .csv` | schema layer: type changes |
| `fuzzy_columns_a.csv / fuzzy_columns_b.csv` | semantic layer: name variations |
| `format_variants_a.csv / format_variants_b.csv` | format layer: date/number formats |
| `missing_rows_a.parquet / missing_rows_b.parquet` | referential layer: row gaps |
| `large_file_a.parquet` (> 500MB, generated) | engine selection: DuckDB path |
| `edge_cases/encoding_latin1.csv` | format layer: encoding detection |
| `edge_cases/mixed_types.csv` | schema layer: type inference |
| `edge_cases/currency_formats.csv` | format layer: currency normalization |

### Test pattern for each layer
```python
def test_statistical_layer_detects_null_rate_increase():
    # Arrange
    engine = PolarsEngine()
    frame_a = engine.read(FIXTURES / "same_schema_same_data.parquet")
    frame_b = engine.read(FIXTURES / "diff_values_high_nulls.parquet")

    # Act
    layer = StatisticalLayer(engine)
    anomalies = layer.compare(frame_a, frame_b)

    # Assert
    null_anomalies = [a for a in anomalies if a.stat_name == "null_rate"]
    assert len(null_anomalies) >= 1
    assert null_anomalies[0].severity in ("warning", "critical")
    assert null_anomalies[0].delta > 0.05
```

### CI test matrix
Run tests against both engines for all layers:
```yaml
strategy:
  matrix:
    engine: [polars, duckdb]
    python-version: ["3.11", "3.12"]
```

Force DuckDB engine in tests via `--memory-threshold-mb 0` flag.

---

## Architecture Decision Records

**ADR 001 — Five-layer comparison model**
Context: existing tools compare files at one layer only (schema diff, or row
diff, or value diff). Real data quality issues span multiple layers
simultaneously and require different detection logic per layer.
Decision: five sequential layers — format, schema, semantic, statistical,
referential. Each layer is independent and produces typed Anomaly objects.
Layers can be run selectively via `--layers` flag.
Tradeoff: more complex than a single-pass comparison. Mitigated by the layer
abstraction — each layer is independently testable and replaceable.

**ADR 002 — Dual engine with automatic threshold switching**
Context: needed to handle both small files (fast in-memory) and large files
(larger than RAM) with identical output.
Decision: Polars for ≤ 500MB (combined file size), DuckDB for > 500MB.
Threshold configurable. Both engines implement the same abstract interface.
Tradeoff: two engines to maintain. Mitigated by the AbstractEngine interface —
adding a third engine (e.g. Spark for distributed files) requires only a new
implementation class.
Swap path to prod: for truly massive files (> 50GB), add a SparkEngine
implementation behind the same interface.

**ADR 003 — Levenshtein + token similarity for fuzzy column matching**
Context: real-world files use inconsistent column naming conventions. Exact
match comparison misses the most common real-world schema drift.
Decision: fuzzy matching using Levenshtein distance normalized by max length,
combined with token-level Jaccard similarity for compound names
(`customer_first_name` vs `firstName`). Threshold: 0.80, configurable.
Tradeoff: fuzzy matching can produce false positives for short column names.
Mitigated by always presenting matches as `suggestion` severity — never
auto-mapping without confirmation.

**ADR 004 — dbt schema.yml as an output format**
Context: a one-time comparison is valuable; permanent pipeline protection is
more valuable. Converting comparison findings into dbt tests means the anomalies
found today become guards against future regressions.
Decision: `--report dbt` generates a `schema.yml` fragment with dbt generic
tests (not_null, accepted_values, relationships) derived from the anomalies
found. The output is a starting point — always review before committing.
Tradeoff: the generated dbt YAML is opinionated and may not match the user's
existing dbt project structure exactly. Document this clearly.

---

## Makefile — required targets

```makefile
make install      # pip install -e ".[dev]"
make test         # pytest with coverage
make test-duckdb  # pytest with --memory-threshold-mb 0 (forces DuckDB engine)
make lint         # ruff + black check + mypy
make fixtures     # generate test fixture files (including large file)
make serve        # start FastAPI REST API server
make docs         # build mkdocs documentation
make build        # build wheel for distribution
make clean        # remove build artifacts
```

---

## README structure — required sections in order

1. **Badge row** — CI, PyPI version, Python versions, license, downloads
2. **One-liner** — "Universal data file comparison and anomaly detection for
   data engineers. Parquet · CSV · Excel · Five layers · Seven output formats."
3. **The problem statement** — two paragraphs: why file diff tools fail for
   data files, and what "same data, different files" actually means
4. **Quick demo** — a realistic scenario: vendor file arrives, 3,000 rows
   missing, revenue column has 12% more nulls, date format changed. Show the
   terminal output with Rich formatting. This is the README's centerpiece.
5. **Installation** — `pip install adv-data-comp`
6. **Quick start** — three examples: CLI, Python API, CI gate
7. **The five layers** — table with layer name, what it detects, example anomaly
8. **Output formats** — table with format name, use case, example
9. **Engine selection** — explain the automatic Polars/DuckDB switching,
   show the threshold config, note the identical output guarantee
10. **LLM explanations (`--explain`)** — show an example explanation output,
    explain Portkey setup, emphasize it's opt-in
11. **CI/CD integration** — show the `--fail-on-critical` pattern, GitHub
    Actions example that fails a PR when a vendor file has critical anomalies
12. **dbt integration** — show `--report dbt` output, explain how to use it
13. **Production swap path** — Polars/DuckDB → Spark for distributed files
14. **Contributing** — link to CONTRIBUTING.md
15. **License** — MIT

---

## Packaging — make it installable

This project should be publishable to PyPI. `pyproject.toml` must configure:

```toml
[project]
name = "adv-data-comp"
version = "0.1.0"
description = "Universal data file comparison and anomaly detection"
requires-python = ">=3.11"
dependencies = [
    "polars>=0.20",
    "duckdb>=0.10",
    "typer>=0.12",
    "rich>=13",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "openpyxl>=3.1",
    "python-Levenshtein>=0.25",
    "pydantic>=2.0",
    "jinja2>=3.1",
    "portkey-ai>=1.0",   # optional, for --explain
]

[project.scripts]
adv-data-comp = "cli.main:app"

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "ruff", "black", "mypy", "httpx"]
```

A PyPI badge in the README showing install count is a strong public signal
that real people use the tool. Aim to publish v0.1.0 when the project is
complete and share it in relevant data engineering communities (dbt Slack,
Data Engineering Weekly newsletter, Reddit r/dataengineering).

---

## Shine checklist

- [ ] `pip install adv-data-comp` works (published to PyPI or TestPyPI)
- [ ] `adv-data-comp compare a.parquet b.csv` runs with zero config
- [ ] Engine switches automatically at 500MB threshold (test with large fixture)
- [ ] All five layers run and produce typed Anomaly objects
- [ ] Fuzzy column matching finds `customer_id` ↔ `cust_id` at threshold 0.80
- [ ] All seven output formats generate without error
- [ ] `--report dbt` output is valid dbt `schema.yml` syntax
- [ ] `--explain` works end-to-end with a Portkey key
- [ ] `--explain` degrades gracefully without a Portkey key (warning, no crash)
- [ ] `--fail-on-critical` exits with code 1 when critical anomalies found
- [ ] `--fail-on-critical` exits with code 0 when no critical anomalies found
- [ ] REST API `/compare` endpoint accepts file uploads and returns JSON
- [ ] Python API `result.has_critical` works correctly
- [ ] README has actual terminal output screenshot (not a placeholder)
- [ ] README has a realistic scenario walkthrough (vendor file example)
- [ ] CI runs tests against both Polars and DuckDB engines
- [ ] CI runs against Python 3.11 and 3.12
- [ ] Test coverage ≥ 85%
- [ ] All four ADRs complete
- [ ] `make fixtures` generates all test fixture files reproducibly
- [ ] `adv-data-comp --help` output is clean and documents all options
- [ ] MIT license present

---

## GitHub profile framing

Repo description:
> Universal data file comparison & anomaly detection — Parquet · CSV · Excel · 5 layers · 7 output formats · DuckDB + Polars

Profile README framing:
> **adv-data-comp** — Practical data engineering tooling. Compares any two
> data files across five semantic layers (format, schema, semantic, statistical,
> referential), auto-switches between Polars and DuckDB based on file size,
> and outputs anomaly reports in seven formats including dbt schema.yml.

This project deliberately shows range — the other three portfolio projects are
AI-native pipelines. This one shows the practical, high-value tooling work that
senior data engineers actually spend time on. Together the four projects cover:
structured data (beacon-lakehouse), unstructured AI knowledge
(client-fact-library), agent quality (agent-eval-platform), and practical
tooling (adv-data-comp).

---

## Questions an AI should ask before writing any code

1. Which layer are we implementing — format, schema, semantic, statistical,
   or referential?
2. Which engine are we targeting — Polars, DuckDB, or both via the abstract
   interface?
3. Which output formatter are we writing?
4. Are we writing the test first (TDD) or the implementation?
5. Which fixture files does this test need — do they exist or do we need to
   generate them?

Do not assume scope. "Write the statistical layer" could mean the layer class,
the DuckDB engine method it calls, the tests, or the formatter output for
statistical anomalies. Confirm before proceeding.
