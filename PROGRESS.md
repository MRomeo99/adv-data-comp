# adv-data-comp — Progress Report

Session date: 2026-07-13
Repo: https://github.com/MRomeo99/adv-data-comp (pushed incrementally, 14 commits on `main`)
Built via strict TDD per `CLAUDE.md`, in dependency-ordered waves (foundation →
five layers → orchestrator → seven formatters → CLI/API/explain → docs/CI),
using parallel subagents for independent pieces and direct implementation for
shared interfaces and integration points.

## What was built

### Foundation
- `pyproject.toml` — full packaging config (hatchling build backend, all
  runtime deps, `dev`/`docs` optional-dependency groups, ruff/black/mypy/
  pytest config).
- `adv_data_comp/models.py` — `Anomaly`, `Severity`, `ColumnProfile`,
  `ColumnType`/`ColumnCategory`, `ComparisonResult` (with `.summary`,
  `.critical`, `.has_critical`, `.schema_match`, and `.to_json`/`.to_yaml`/
  `.to_html`/`.to_markdown`/`.to_csv`/`.to_dbt_yaml` convenience methods),
  `ComparisonMeta`/`FileMeta`.
- `adv_data_comp/config.py` — `ComparisonConfig`, `OutputFormat`.
  `memory_threshold_mb` reads `ADV_DATA_COMP_MEMORY_THRESHOLD_MB` as its
  default (a spec requirement the original scaffold missed).
- `adv_data_comp/__init__.py` — exports the public Python API
  (`Comparator`, `ComparisonConfig`, `OutputFormat`, `Anomaly`,
  `ComparisonResult`, `Severity`) per the spec's Python API contract.

### Dual engine — `adv_data_comp/engine/`
`AbstractEngine` (`read`, `schema`, `profile_column`, `row_count`,
`find_missing_keys`) implemented identically by `PolarsEngine` (in-memory)
and `DuckDBEngine` (streaming, SQL-backed). `select_engine()` auto-picks
based on combined file size vs. threshold. Every engine-level test is
parametrized over both engines to enforce ADR-002's identical-output
guarantee.

### Five comparison layers — `adv_data_comp/layers/`
Format, Schema, Semantic (fuzzy Levenshtein+token matching), Statistical
(numeric/string/date distribution checks), Referential (key-based row
diffing) — each an `AbstractLayer.compare(engine, frame_a, frame_b, config,
column_mapping=None) -> list[Anomaly]`. Built in parallel by 5 agents
against the shared interface; `FormatLayer` additionally takes the raw file
paths at construction (needed for byte-level encoding/line-ending checks
the frame interface can't expose).

### Orchestration — `adv_data_comp/comparator.py`
`Comparator(config).compare(file_a, file_b)` runs the configured layers in
order, isolates per-layer failures as a warning anomaly (never aborts the
run), threads the semantic layer's fuzzy `suggested_mapping` into the
statistical/referential layers via `column_mapping`, applies
`severity_filter`, and populates `ComparisonMeta`.

### Seven output formatters — `adv_data_comp/formatters/`
JSON, YAML, CSV, Markdown, Terminal (Rich, matches the spec's exact
terminal layout), HTML (self-contained, `<details>` cards, inline-SVG
statistical charts, fully escaped), dbt `schema.yml` (heuristic MVP:
`not_null` from statistical null-rate anomalies, `unique` from referential
duplicate-key anomalies — `relationships`/`accepted_values` deliberately
skipped and documented in ADR 004).

### CLI — `cli/main.py` (Typer)
`compare` (all spec'd flags, `--config` YAML merge with CLI-flag
precedence, `--severity` as a minimum-severity filter, `--fail-on-critical`
exit code), `profile`, `schema`, `formats`, `version`.

### REST API — `adv_data_comp/serve/{app,routes}.py` (FastAPI)
`POST /compare` (multipart upload, `json`/`yaml` response), `GET /health`,
`/version`, `/formats`.

### LLM explain — `adv_data_comp/explain/portkey_explainer.py`
`explain_anomalies()` batches one Portkey call per layer for critical/
warning anomalies only, via an injectable `client_factory` (the real
Portkey wiring is never exercised by tests). Missing prompt ID → warning,
continues without explanations. Per-layer failure isolation.

### Fixtures, docs, packaging
- `adv_data_comp/dev/generate_fixtures.py` (`make fixtures`) generates every
  fixture pair from CLAUDE.md's matrix table; the >500MB engine-selection
  fixture is opt-in (`--large`) and gitignored.
- `docs/adr/00{1..4}-*.md` — the four ADRs.
- `README.md` — full 15-section structure, including a quick-demo terminal
  block captured from an actual `adv-data-comp compare` run (not a mockup).
- `Makefile`, `.env.example`, `mkdocs.yml`, `.github/workflows/ci.yml`
  (python 3.11/3.12 × polars/duckdb matrix, `--cov-fail-under=85`, separate
  lint job).

## Test results
```
292 passed
Coverage: 97% (well above the 85% shine-checklist target)
ruff check .        -- all checks passed
black --check .     -- clean
mypy adv_data_comp cli -- no issues (35 source files)
```

## Real bugs found by manually running the software (not just the test suite)
1. **CLI crashed on Windows** (`UnicodeEncodeError`) — the default console
   codec (cp1252) can't encode Rich's emoji/box-drawing output. The
   `CliRunner`-based test suite never caught this since it captures output
   through an in-memory stream, not a real codec. Fixed by forcing UTF-8 on
   stdout/stderr at CLI startup.
2. **`from adv_data_comp import Comparator` didn't work** — the spec's
   Python API examples all assume top-level exports, but `__init__.py` only
   had `__version__` until this was caught while writing the README's code
   samples and verified with a dedicated test.

Both were found by actually exercising the CLI/API rather than trusting
green tests alone, and both are now covered by regression tests
(`tests/test_package_api.py`; the encoding fix has no dedicated test since
it's a platform-codec issue, not business logic — noted as a gap below).

## Assumptions / design decisions
1. **CLAUDE.md's own final section** ("Questions an AI should ask before
   writing any code") says not to assume scope — respected by building the
   foundation first and checking in before committing to the full build-out,
   per explicit user confirmation mid-session.
2. **`AbstractLayer`/`AbstractFormatter`/`ComparisonConfig` contracts** were
   defined once, upfront, specifically so 5 parallel layer agents and 5
   parallel formatter agents could build against a stable interface without
   colliding.
3. **Semantic layer's fuzzy matching** combines a Levenshtein ratio with a
   Levenshtein-*tolerant* token Jaccard (not strict token equality) —
   required to actually satisfy the spec's own `CustomerID`/`customer_id`/
   `cust_id`/`CUST_ID` example scoring ≥0.80; documented in ADR 003.
4. **dbt formatter** only maps `not_null` and `unique` tests — the two
   anomaly types with an unambiguous single-file mapping. `relationships`
   and `accepted_values` are deliberately skipped rather than fabricated;
   see ADR 004.
5. **Statistical layer's "future date presence" check is omitted** — it
   would require a live `datetime.now()` reference, which conflicts with
   this codebase's deterministic-test requirement.
6. **No live Portkey/PyPI credentials exist** — `--explain` is untested
   against a real Portkey account (by design — tests always inject a fake
   client), and the package hasn't been published anywhere.

## Known gaps / what remains
- **`--explain` has never been run against a real Portkey account** — the
  injected-client design means the actual SDK call shape
  (`portkey_ai.Portkey(...).prompts.completions.create(...)`) is a
  best-effort based on the installed package's signatures, not verified
  against a live response.
- **The >500MB engine-selection fixture has never actually been
  generated** — `generate_fixtures(include_large=True)` is tested with a
  tiny row count standing in for it; running it for real (`make fixtures
  ARGS=--large`) would take real time/disk and wasn't done in this session.
- **CI has not been observed running on GitHub** — `.github/workflows/ci.yml`
  was pushed to `main`, which should trigger it, but this environment has no
  `gh` CLI or browser access to confirm the Actions run actually succeeds
  remotely. Check the repo's Actions tab.
- **Not published to PyPI/TestPyPI** — the shine-checklist's `pip install
  adv-data-comp` item is unmet; `pip install -e .` from source works.
- **No dedicated regression test for the Windows UTF-8 console fix** — it's
  a platform-codec issue that's hard to unit test portably; only manually
  verified.
- **`serve` isn't wired into the CLI** — `adv-data-comp serve` (to launch
  the FastAPI app via uvicorn) isn't a CLI command; the Makefile's `make
  serve` target calls `uvicorn adv_data_comp.serve.app:app` directly instead.
- **`adv_data_comp/explain/prompts/`** (mentioned in CLAUDE.md's file
  structure as "direct mode fallback prompts only") was never populated —
  the explainer's Portkey Prompt Library approach doesn't currently have a
  local fallback prompt file.

## Next recommended steps
1. Get a real Portkey account, verify `--explain` end-to-end, and adjust the
   SDK call shape/config in `portkey_explainer.py` if it doesn't match.
2. Run `make fixtures ARGS=--large` once, confirm the DuckDB engine path
   activates correctly on a real >500MB comparison, and record the runtime.
3. Confirm the GitHub Actions run is green on `main` (or fix whatever the
   matrix reveals — this is the first time the full python×engine matrix
   runs anywhere but this one Windows dev machine).
4. Publish to TestPyPI, then PyPI, once the above are confirmed.
5. Populate `adv_data_comp/explain/prompts/` with a local fallback prompt if
   direct (non-Portkey-hosted) prompt mode is wanted.
