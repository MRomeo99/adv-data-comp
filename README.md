# adv-data-comp

![CI](https://github.com/MRomeo99/adv-data-comp/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/badge/PyPI-not%20yet%20published-lightgrey)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Universal data file comparison and anomaly detection for data engineers.**
Parquet · CSV · Excel · Five layers · Seven output formats · DuckDB + Polars

## The problem

File diff tools compare bytes, which is useless for data: a re-exported
Parquet file with the same values in a different row order looks like a
100% diff, and reordered CSV columns look like a rewrite. Data validation
tools go the other way — they check a single file against a schema, but
can't tell you that *this* file drifted from *that* one.

What data engineers actually need to answer is a comparison question: "these
two files are supposed to represent the same data — what's wrong with them,
and why?" That means checking format conventions, schema structure, column
semantics, statistical distributions, and row-level referential integrity —
together, because a real vendor-file regression usually shows up across
more than one of those at once.

## Quick demo

A vendor sends a monthly customer export. This month's file is missing 3,000
rows, the `revenue` column picked up a wave of nulls, and the date format
changed. Running `adv-data-comp` against last month's file catches all of
it in one pass — this is real output from this repository, not a mockup:

```
adv-data-comp ──────────────────────────────────────────────────────────────
Comparing: customers_jan.parquet → customers_feb.csv
Engine: polars
─────────────────────────────────────────────────────────────────────────────

🔴 CRITICAL (2)
  [Statistical] Row count differs by 60.0%: 5000 rows in file A vs 2000 rows in file B
  [Referential] 3000 rows in file A not found in file B

🟡 WARNING (6)
  [Schema] Null rate for 'revenue' shifted from 0.0% to 13.7%
  [Statistical] Stddev changed by more than 50% — indicates distribution shift (1443.52 (A) vs 577.49 (B))
  [Statistical] Null rate differs: 0.0% (A) vs 13.7% (B)
  [Statistical] Top-10 most frequent values shifted (overlap 0%)
  [Referential] 274 matched rows differ in column 'revenue'
  [Referential] 2000 matched rows differ in column 'signup_date'

🔵 INFO (3)
  [Format] Column 'signup_date': date format differs between files
  [Statistical] Min/max range shifted: [1, 5000] (A) vs [1, 2000] (B)
  [Statistical] Min/max range shifted: [50.07, 499.98] (A) vs [50.08, 499.46] (B)

─────────────────────────────────────────────────────────────────────────────
Total anomalies: 11  │  Runtime: 0.0s  │  Engine: polars
```

```bash
adv-data-comp compare customers_jan.parquet customers_feb.csv --key customer_id
```

## Installation

```bash
pip install adv-data-comp   # not yet published to PyPI — see "Status" below
```

For now, install from source:

```bash
git clone https://github.com/MRomeo99/adv-data-comp.git
cd adv-data-comp
pip install -e ".[dev]"
```

## Quick start

**CLI:**
```bash
adv-data-comp compare file_a.parquet file_b.csv --key customer_id --report html
```

**Python API:**
```python
from adv_data_comp import Comparator

result = Comparator().compare("file_a.parquet", "file_b.csv")
print(result.summary)          # {'critical': 2, 'warning': 4, 'info': 2, 'suggestion': 1}
print(result.has_critical)     # True
```

**CI gate:**
```python
from adv_data_comp import Comparator
from adv_data_comp.config import ComparisonConfig

def validate_vendor_file(expected: str, received: str) -> None:
    result = Comparator(ComparisonConfig(key="id")).compare(expected, received)
    if result.has_critical:
        raise SystemExit(f"Vendor file failed validation: {result.summary['critical']} critical anomalies")
```

## The five layers

| Layer | What it detects | Example anomaly |
|---|---|---|
| Format | Encoding, line endings, decimal/currency/date/boolean conventions | `revenue`: decimal separator `,` → `.` |
| Schema | Column count/name/type/nullability/order differences | `customer_id` type: `int64` → `string` |
| Semantic | Fuzzy column-name matches, type-coercion candidates, unit mismatches | `cust_email` ↔ `customer_email` (similarity 0.91) |
| Statistical | Distribution shifts on matched columns (null rate, mean, stddev, top values, date ranges) | `revenue`: null rate 2.1% → 14.8% |
| Referential | Missing/new rows, duplicate keys, matched-row value diffs (requires `--key`) | 3,847 rows in file A not found in file B |

Layers are independent — a failure in one never skips the others — and each
is selectable via `--layers format,schema,...`.

## Output formats

| Format | Use case |
|---|---|
| Terminal (always shown) | Interactive use, quick triage |
| `html` | Self-contained shareable report, no external dependencies |
| `json` | Programmatic use in CI/CD pipelines |
| `yaml` | dbt-style config-as-code workflows |
| `markdown` | Automated PR comments (GFM tables + emoji severity) |
| `csv` | Further analysis in Excel/BI tools, one row per anomaly |
| `dbt` | Converts findings into permanent `schema.yml` pipeline tests |

Multiple formats can be generated in one run: `--report html --report json --report dbt`.

## Engine selection

Every comparison is served by either **Polars** (in-memory, fast for small/
medium files) or **DuckDB** (streaming, handles files larger than RAM) —
selected automatically from the combined file size, with an identical
`AbstractEngine` interface behind both, so the caller never needs to know
which one ran:

```python
def select_engine(file_a, file_b, threshold_mb):
    total_mb = (file_a.stat().st_size + file_b.stat().st_size) / 1_048_576
    return PolarsEngine() if total_mb <= threshold_mb else DuckDBEngine()
```

Default threshold: 500MB combined, configurable via `--memory-threshold-mb`
or `ADV_DATA_COMP_MEMORY_THRESHOLD_MB`. See [ADR 002](docs/adr/002-dual-engine-threshold.md)
for why 500MB and the Spark swap-path for files beyond DuckDB's comfort zone.

## LLM explanations (`--explain`)

Opt-in. Critical and warning anomalies are enriched with a plain-English
explanation and suggested remediation via a Portkey-routed LLM call
(`gemini-2.5-flash`, falling back to `gpt-4o-mini`), batched once per layer
to minimize API calls:

```
Column `revenue`: null rate increased from 2.1% to 14.8% (+12.7 points)

Explanation: This likely indicates a join failure or filter change in the
upstream pipeline producing file B. Check recent changes to join conditions
or WHERE clauses.
```

Requires `PORTKEY_API_KEY` and `PORTKEY_PROMPT_EXPLAIN_ID` (see
`.env.example`). Without a prompt ID, `--explain` prints a warning and the
comparison still runs to completion, unexplained — the tool never requires
an API key for its core five layers or seven output formats.

## CI/CD integration

```bash
adv-data-comp compare expected.parquet received.csv --key id --fail-on-critical --quiet --report markdown
```

`--fail-on-critical` exits 1 when any critical anomaly is found (0
otherwise); `--quiet` suppresses terminal output for log-noise-sensitive CI
runners. Example GitHub Actions step that fails a PR when a vendor file
regresses:

```yaml
- name: Validate vendor file
  run: |
    pip install adv-data-comp
    adv-data-comp compare golden/customers.parquet incoming/customers.csv \
      --key customer_id --fail-on-critical --report markdown --output-dir ./
```

## dbt integration

```bash
adv-data-comp compare customers_jan.parquet customers_feb.csv --key customer_id --report dbt
```

Produces a `schema.yml` fragment with `not_null`/`unique` tests derived from
the anomalies found (e.g. a column whose null rate increased gets a
`not_null` test) — a one-time comparison becomes permanent pipeline
protection. See [ADR 004](docs/adr/004-dbt-output-format.md) for exactly
which anomaly types map to which dbt test, and which are deliberately
skipped.

## Production swap path

Polars/DuckDB cover files up to tens of GB comfortably. For truly massive
(50GB+) or distributed files, add a `SparkEngine` behind the same
`AbstractEngine` interface used by `PolarsEngine`/`DuckDBEngine` — no layer
code changes, since every layer only ever calls the abstract interface.

## Architecture Decision Records

- [ADR 001 — Five-layer comparison model](docs/adr/001-five-layer-model.md)
- [ADR 002 — Dual engine with automatic threshold switching](docs/adr/002-dual-engine-threshold.md)
- [ADR 003 — Levenshtein + token similarity for fuzzy column matching](docs/adr/003-fuzzy-matching-approach.md)
- [ADR 004 — dbt schema.yml as an output format](docs/adr/004-dbt-output-format.md)

## Status

This project is under active development and not yet published to PyPI.
See `PROGRESS.md` for exactly what's built, what's tested, and what
remains.

## Contributing

Issues and PRs welcome. This project follows strict TDD — see `CLAUDE.md`
for the full spec and testing conventions before opening a PR.

## License

MIT — see [LICENSE](LICENSE).
