# Design Decisions — adv-data-comp

**Perspective:** Senior Data Engineer
**Scope:** Architectural choices, patterns, alternatives considered, and honest trade-off analysis across the comparison engine, layer pipeline, and the three interfaces built on top of it.

> The individual technical choices (five-layer model, Polars/DuckDB threshold, Levenshtein+token fuzzy matching, dbt output format) each have a dedicated Architecture Decision Record in [`docs/adr/`](adr/). This document covers the *structural patterns* that sit above those — the "why did we shape the codebase this way" rather than "why did we pick this algorithm."

---

## Table of Contents

1. [Three interfaces, one engine](#1-three-interfaces-one-engine)
2. [Strategy objects for engine/layer/formatter, not one big comparison function](#2-strategy-objects-for-enginelayerformatter-not-one-big-comparison-function)
3. [One flexible `Anomaly` type instead of a typed subclass per check](#3-one-flexible-anomaly-type-instead-of-a-typed-subclass-per-check)
4. [Layers fail independently — the Comparator never fails fast](#4-layers-fail-independently--the-comparator-never-fails-fast)
5. [Column mapping as an explicit, functional handoff](#5-column-mapping-as-an-explicit-functional-handoff)
6. [DuckDB frames wrap a connection + view name, not a materialized Polars frame](#6-duckdb-frames-wrap-a-connection--view-name-not-a-materialized-polars-frame)
7. [Pydantic models throughout, not dataclasses](#7-pydantic-models-throughout-not-dataclasses)
8. [Zero live network calls in the test suite — dependency injection at every external boundary](#8-zero-live-network-calls-in-the-test-suite--dependency-injection-at-every-external-boundary)
9. [Fixture generation via script, with the >500MB file deliberately opt-in](#9-fixture-generation-via-script-with-the-500mb-file-deliberately-opt-in)
10. [REST uploads land in a temp directory, never raw paths](#10-rest-uploads-land-in-a-temp-directory-never-raw-paths)
11. [Manual verification surfaced two real bugs the test suite missed](#11-manual-verification-surfaced-two-real-bugs-the-test-suite-missed)

---

## 1. Three interfaces, one engine

### The decision

The CLI (`cli/main.py`), the REST API (`adv_data_comp/serve/routes.py`), and the Python library (`adv_data_comp.Comparator`) are all thin callers of the exact same `Comparator.compare()`. None of them re-implements comparison logic, re-parses anomalies, or maintains its own notion of severity/summary counts.

```
        CLI            REST API           Python API
         │                 │                   │
         └─────────────────┼───────────────────┘
                            ▼
                     Comparator.compare()
                            │
                            ▼
              five layers × AbstractEngine
```

### Why not alternatives

**A FastAPI service layer with its own comparison logic, calling into a shared "core" library only for utilities:** this is the pattern that quietly rots — the API grows a special case, the CLI grows a different one, and eighteen months later the two interfaces disagree about what counts as a critical anomaly. Making the API a *pure* caller of `Comparator` means that guarantee is structural, not a matter of code review discipline.

**Separate lightweight comparison logic in the CLI for speed** (skip building a full `ComparisonResult`, just print anomalies as they're found): rejected because it would mean the CLI's `--fail-on-critical` and the API's `has_critical` field could theoretically disagree if the two paths ever drifted. One code path, one behavior, is worth the (negligible) overhead of building the full result object even when the caller only wants the exit code.

### What we gave up

The REST API's `/compare` response is exactly `ComparisonResult` serialized to JSON/YAML — it can't offer an API-specific shape (e.g., paginated anomalies for huge result sets) without either changing the shared model or adding a translation layer. For the current scale (single-comparison, synchronous requests) this is the right trade; a high-volume streaming API would need to revisit it.

---

## 2. Strategy objects for engine/layer/formatter, not one big comparison function

### The decision

Three parallel `Abstract*` interfaces — `AbstractEngine`, `AbstractLayer`, `AbstractFormatter` — each with a single method (`read`/`schema`/`profile_column`/`row_count`/`find_missing_keys`; `compare(engine, frame_a, frame_b, config, column_mapping=None)`; `format(result)`). The `Comparator` is an orchestrator that knows the sequencing but contains none of the actual comparison or rendering logic.

### Why not alternatives

**A single `compare_files(a, b, config)` function with internal `if engine == "duckdb"` branches per check:** this is what a first draft of this tool looks like, and it's also the reason most one-off comparison scripts can't be extended. Adding a sixth layer or a third engine means editing one enormous function and re-testing everything downstream of the edit. The interface split means five layer implementations and two engine implementations were built *in parallel by independent workers* against a contract that was fixed before any of them started — that parallelism wouldn't have been possible with a monolithic function, because every worker would have been editing the same file.

**Inheritance-based layer variants** (a `BaseStatisticalLayer` that `NumericStatisticalLayer`/`StringStatisticalLayer` extend): rejected in favor of composition — each layer's `compare()` internally dispatches by column category (`int`/`float`/`string`/`date`) rather than being subclassed per category. A single column often needs multiple check *kinds* (a numeric column gets null-rate, mean-shift, and outlier checks all in one pass), so category-based inheritance would have meant either multiple inheritance or an awkward "IS-A" model for something that's really "for this data, run these checks."

### What we gave up

The interfaces are intentionally narrow (`compare()` takes exactly these five arguments). A future check that needs information none of the five layers currently pass — e.g., a cross-file join key inferred by heuristics rather than `--key` — would require widening the `AbstractLayer` signature, which touches all five implementations. That's a real cost, paid once, in exchange for every layer being independently testable and swappable today.

---

## 3. One flexible `Anomaly` type instead of a typed subclass per check

### The decision

Every layer produces the same `Anomaly` model: `layer`, `severity`, `column`, `message`, a freeform `evidence: dict`, and an optional `explanation`. There is no `FormatAnomaly`/`SchemaAnomaly`/`StatisticalAnomaly` class hierarchy, even though the original spec's illustrative JSON examples suggest per-check fields like `stat_name`/`value_a`/`value_b`/`delta`.

### Why not alternatives

**A typed subclass per check** (`SchemaAnomaly(column, type_a, type_b, null_rate_a, null_rate_b)`, etc., as the spec's prose literally names them) would give every formatter compile-time-checked access to exactly the fields each check produces. It was rejected because there are on the order of 30 distinct checks across five layers, each with a different evidence shape — a full type hierarchy would mean 30 classes, and every formatter (seven of them) would need a branch per class to render it. The freeform `evidence` dict lets every formatter iterate anomalies uniformly (`for a in result.anomalies: render(a.layer, a.severity, a.message, a.evidence)`) without a dispatch table.

**A fully dynamic `dict` for the whole `Anomaly`, no Pydantic model at all:** rejected — losing `severity` as a validated enum would mean a typo (`"critcal"`) silently produces an anomaly that never sorts into any severity bucket, discovered only when someone notices `result.summary["critical"]` under-counts. The four fixed top-level fields are validated; only the check-specific detail is a bag.

### What we gave up

Nothing in the type system stops a layer from putting inconsistent keys in `evidence` for what's conceptually the same kind of anomaly (e.g., the null-rate check and the mean-shift check both being in "statistical" but not sharing a naming convention for their `_a`/`_b` suffix pairs). This was mitigated procedurally — the same evidence-key conventions were specified explicitly in each layer-building agent's brief — but it's a convention, not a compiler-enforced guarantee. The dbt formatter's `not_null`/`unique` mapping (see [ADR 004](adr/004-dbt-output-format.md)) has to grep for specific evidence keys (`null_rate_a`, `duplicate_count`) rather than pattern-matching on a type, which is the direct cost of this choice.

---

## 4. Layers fail independently — the Comparator never fails fast

### The decision

`Comparator.compare()` wraps every layer's `.compare()` call in a `try/except Exception`. A layer that raises doesn't abort the comparison — it's recorded as a single `warning`-severity anomaly (`Layer 'X' failed: <exception>`, with `evidence={"error": ..., "error_type": ...}`) and the remaining layers still run.

```python
try:
    layer_anomalies = layer.compare(engine, frame_a, frame_b, self.config, mapping_arg)
except Exception as exc:  # layers must never abort the run
    layer_anomalies = [Anomaly(layer=layer_name, severity=Severity.WARNING, ...)]
```

### Why not alternatives

**Fail the whole comparison on any layer exception:** this is the obvious default in most pipelines, and it's wrong here specifically because the five layers are documented (in [ADR 001](adr/001-five-layer-model.md)) as independent — "a failure in layer 3 does not skip layer 4" is a spec requirement, not an implementation detail. A referential-layer bug on a file with a malformed key column shouldn't hide a critical schema mismatch that layer 2 already found.

**A per-layer `--strict` flag that turns exceptions into hard failures:** considered as a middle ground, but rejected for this version — it would mean the CLI's exit-code semantics depend on two independent flags (`--fail-on-critical` and a hypothetical `--strict`) instead of one, for a failure mode (a layer crashing) that should be rare and is already visible in the output as a warning anomaly.

### What we gave up

A layer that crashes doesn't get a second chance or a partial result — the whole layer's contribution for that comparison is exactly one warning anomaly, not "everything except the one column that broke." For the current checks (which are mostly independent per-column loops) a single bad column could in principle be caught more granularly inside each layer; this session's layers only catch failures at the `Comparator` level, not per-column inside `_check_numeric_column`/etc. That's a reasonable next increment if a specific check turns out to be fragile on real-world data.

---

## 5. Column mapping as an explicit, functional handoff

### The decision

The semantic layer's fuzzy-match suggestions (`evidence["suggested_mapping"]`) are picked up by the `Comparator` — not consumed directly by later layers — and turned into a plain `dict[str, str]` (`{column_in_file_b: column_in_file_a}`) that's passed as an explicit `column_mapping` argument into the statistical and referential layers' `compare()` calls.

### Why not alternatives

**Layers read each other's anomalies directly** (statistical layer scans `previous_anomalies` for semantic suggestions itself): rejected because it would mean every layer needs to know the internal evidence-key conventions of every *other* layer it might depend on, defeating the point of the `AbstractLayer` contract being narrow and uniform. Centralizing the "read semantic anomalies, build a mapping" step in the `Comparator` means only one piece of code needs to know that convention.

**A shared mutable "comparison context" object that layers write into and read from:** this is the more common pattern in ETL-style frameworks (a context dict threaded through every stage). Rejected here because it makes data flow implicit — you can't tell from a layer's signature what it depends on or produces. An explicit `column_mapping` parameter (present even when empty) means `StatisticalLayer.compare()`'s signature *is* its dependency list.

### What we gave up

Only two layers currently consume `column_mapping` (`_COLUMN_MAPPING_CONSUMERS = {"statistical", "referential"}`), and that set is a hardcoded constant in `comparator.py` rather than something each layer declares about itself. Adding a sixth layer that also wants fuzzy-matched columns means remembering to add it to that set — a small, centralized, but easy-to-forget piece of coupling.

---

## 6. DuckDB frames wrap a connection + view name, not a materialized Polars frame

### The decision

`PolarsEngine.read()` returns a `polars.DataFrame` directly. `DuckDBEngine.read()` instead returns a small `DuckDBFrame` dataclass (`con: DuckDBPyConnection`, `view_name: str`) — the file is registered as a SQL view (`CREATE OR REPLACE VIEW frame_N AS SELECT * FROM read_parquet(...)`) on a connection owned by that `DuckDBEngine` instance, and every subsequent operation (`profile_column`, `find_missing_keys`, the referential layer's row-level join) is a SQL query against that view.

### Why not alternatives

**Convert DuckDB query results to Polars/Arrow immediately so both engines return the same frame type:** this was the obvious first idea, and it defeats the entire point of DuckDB in this architecture. DuckDB is selected specifically for files too large to fit in memory ([ADR 002](adr/002-dual-engine-threshold.md)); materializing the read into a Polars DataFrame at read-time would mean loading the whole file into memory anyway, just one step later than if Polars had been used directly. The `AbstractEngine` interface exists precisely so that "what shape is the frame" never needs to be uniform across engines — every method takes an opaque `EngineFrame` and only that engine's own implementation ever inspects it.

**One shared DuckDB connection at module scope instead of one per `DuckDBEngine` instance:** rejected because two independent comparisons running concurrently (e.g., two requests hitting the REST API at once) would then share view names and could collide or leak state between unrelated comparisons. A connection — and its views — is scoped to one `DuckDBEngine`, which is scoped to one `Comparator.compare()` call.

### What we gave up

Passing a `DuckDBFrame` from one comparison into a different `DuckDBEngine` instance's methods would silently fail or produce wrong results (the view only exists on the connection that created it) — the type system doesn't prevent this misuse; it relies on `DuckDBEngine` always being the thing that both reads and later queries its own frames, which `Comparator` guarantees by construction but nothing enforces at the `AbstractEngine` interface level.

---

## 7. Pydantic models throughout, not dataclasses

### The decision

Every data-carrying type in this codebase — `Anomaly`, `ComparisonResult`, `ComparisonConfig`, `ColumnProfile`, `ColumnType`, `FileMeta`, `ComparisonMeta` — is a Pydantic `BaseModel`, not a `dataclass` or a plain dict.

### Why not alternatives

**`dataclasses` (stdlib, zero dependency cost):** would have been sufficient for internal data-passing between layers, and are faster to construct. Rejected because every one of these types eventually needs to leave the process boundary — as JSON over the REST API, as a JSON/YAML file on disk, as a validated config loaded from a YAML file passed to `--config`. Pydantic gives `model_dump(mode="json")` and `ValidationError` on malformed input for free; dataclasses would have needed a hand-written serializer and a hand-written validator, most likely duplicated across the CLI's `--config` loader and the API's request parsing.

**Plain dicts passed between layers** (what a quick script would do): rejected for the same reason the freeform `Anomaly.evidence` field is *contained* rather than the whole object being a dict (see §3) — `severity="warning"` typos need to fail loudly at construction time, not silently produce a `KeyError` three layers later.

### What we gave up

Pydantic validation has a real per-object construction cost compared to a dataclass or a dict literal. For the anomaly volumes this tool actually produces (tens to low hundreds per comparison, not millions), that cost is immeasurable; it would need reconsidering if this pipeline were ever asked to validate anomaly objects at row-level granularity across a very wide file.

---

## 8. Zero live network calls in the test suite — dependency injection at every external boundary

### The decision

Nothing in `pytest tests/` ever makes a real HTTP request, hits a real LLM API, or depends on a real Portkey/Stripe/whatever account existing. The one place this codebase talks to an external LLM service — `adv_data_comp/explain/portkey_explainer.py` — takes an injectable `client_factory: Callable[[], Any]` parameter; the real Portkey-backed factory (`_default_client_factory`) is only ever invoked when a caller doesn't supply one, and no test supplies one.

### Why not alternatives

**Recorded HTTP cassettes (VCR-style) against a real Portkey call made once, replayed thereafter:** a common and reasonable pattern, rejected here mainly because this project has no Portkey account to record against in the first place, and a cassette would encode assumptions about the exact request/response shape that can't be verified without one anyway. The honest state of `--explain` is: it's built against the installed `portkey-ai` SDK's actual method signatures (verified by reading the package, not guessed), but never exercised end-to-end — that's stated plainly in `PROGRESS.md` rather than papered over with a fake-looking cassette.

**Mocking at the `portkey_ai` import level (`unittest.mock.patch("portkey_ai.Portkey")`) instead of an injected factory:** would achieve the same test isolation, but couples every test to the exact import path and constructor signature of a third-party SDK. The `client_factory` seam means the *tests* define the contract (`.explain(anomalies_batch, prompt_id) -> dict[int, str]`) and the real Portkey wiring has to conform to it — not the other way around.

### What we gave up

Because the real Portkey path is never exercised, a change to the `portkey-ai` package's API (a renamed method, a different response shape) would only be caught the first time someone actually runs `--explain` with real credentials — not by CI. This is the same trade-off the Comparator's per-layer failure isolation makes safer in practice (a broken `--explain` call degrades to a warning, not a crash — see [ADR item on graceful degradation](../README.md#llm-explanations---explain)), but it's still a real gap between "tests pass" and "the feature works against the live service."

---

## 9. Fixture generation via script, with the >500MB file deliberately opt-in

### The decision

`adv_data_comp/dev/generate_fixtures.py` builds every fixture pair from the spec's fixture matrix (baseline, value-drift, schema-change, fuzzy-column-name, format-variant, missing-rows, and three encoding/type/currency edge cases) programmatically, and commits the *small* results directly to `tests/fixtures/`. The one fixture explicitly meant to exceed the 500MB engine-selection threshold is generated by the same script behind an `include_large=False` default / `--large` CLI flag, and is gitignored rather than committed.

### Why not alternatives

**Commit a real >500MB Parquet file to the repository:** the most literal reading of "generate a >500MB fixture," and the wrong one for a public git repository — every clone pays that 500MB forever, `git blame` on that file is meaningless, and GitHub's soft file-size guidance starts complaining well before 500MB. A script that *can* produce that file on demand, deterministically, from a one-line row count parameter, gives the same testing capability without the storage cost.

**Hand-written static CSV/Parquet fixtures per test file** (each layer's own tests build tiny fixtures inline via `tmp_path`, which is in fact what actually happens today) instead of *also* maintaining a shared fixture matrix: both exist in this codebase for different reasons — the inline `tmp_path` fixtures are for layer-specific unit tests that need precise control over one specific anomaly; the shared `tests/fixtures/` matrix exists because the spec explicitly calls for a `make fixtures` target and names these files, presumably for manual exploration/demoing rather than being consumed by the pytest suite itself. Neither replaces the other.

### What we gave up

The `--large` fixture has never actually been generated as part of this build (see `PROGRESS.md`) — only its code path, at a tiny stand-in row count, is covered by a test. The engine-selection *logic* is fully tested (`select_engine` with a mocked/tiny file size crossing the threshold); what's untested is the DuckDB engine's actual behavior reading a file that's genuinely too large to comfortably fit in memory on a real machine.

---

## 10. REST uploads land in a temp directory, never raw paths

### The decision

`POST /compare` accepts two `UploadFile` form fields. Each is written to `Path(tempfile.TemporaryDirectory()) / Path(upload.filename).name` — note `.name`, not the raw `upload.filename` — before `Comparator.compare()` ever sees a path.

### Why not alternatives

**Read the upload into memory and pass bytes directly to a modified `Comparator` API:** would avoid a disk round-trip, but every engine's `read()` dispatches on `path.suffix` and every third-party reader (`pl.read_csv`, `duckdb.read_parquet`) is a *file*-oriented API first and a bytes-oriented one second (Polars supports in-memory buffers; DuckDB's `read_parquet`/`read_csv_auto` SQL functions expect a path or glob). Keeping `Comparator.compare(file_a, file_b)` path-based means the CLI, the REST API, and the Python library all call the exact same method signature (see §1) — an in-memory variant would need either a second `Comparator` method or a fork in behavior between "path mode" and "bytes mode."

**Use the client-supplied filename as-is for the temp file path:** rejected outright — a filename like `../../etc/passwd` or an absolute path would either fail unpredictably or, worse, write outside the intended temp directory depending on how the receiving code joins paths. Stripping to `Path(...).name` keeps only the base filename (and, critically, its extension, which the engine needs for format detection) while discarding any directory traversal component.

### What we gave up

Every `/compare` request pays a full write-to-disk-then-read-back round trip for both files, even for small comparisons that would fit comfortably in memory. For the synchronous, one-comparison-per-request usage this API is designed for (see §1's discussion of what the REST API is *not* — a high-volume streaming endpoint), that cost is acceptable; a high-throughput deployment would want to special-case small uploads to skip the disk round trip.

---

## 11. Manual verification surfaced two real bugs the test suite missed

### The decision

Before considering the CLI "done," it was actually run — `adv-data-comp compare customers_jan.parquet customers_feb.csv --key customer_id` against a realistic vendor-file scenario — rather than relying on the 292-test, 97%-coverage suite as sufficient proof the feature worked.

### What that caught

1. **`UnicodeEncodeError` on a real Windows console.** The Rich-rendered terminal output (emoji severity markers, box-drawing rule characters) crashed when `typer.echo()` hit the default `cp1252` codec. Every CLI test uses Typer's `CliRunner`, which captures output through an in-memory stream that never touches a real console codec — so 12 passing CLI tests, including ones that assert on the terminal output's content, gave zero signal about this failure mode.
2. **`from adv_data_comp import Comparator` raised `ImportError`.** The package's `__init__.py` only exported `__version__` until this was caught while writing the README's own Python API code samples and confirmed with a dedicated test. Every *internal* test imports from the specific submodule (`from adv_data_comp.comparator import Comparator`), so the top-level public API surface — the thing an actual user's `pip install`-ed code would use — was never exercised by the test suite at all.

### Why this matters as a process point, not just a bug list

A green test suite answers "does the code I wrote do what I told the tests to check for." It does not answer "does a user typing the documented command on their actual machine get a working result" — those are different claims, and the gap between them is exactly where both of these bugs lived. Neither would have been caught by more unit tests of the same kind already in the suite; both needed the software to actually run outside of a test harness.

### What we gave up

There is still no *automated* regression test for the Windows console-encoding fix — it's a platform/codec interaction that's awkward to assert on portably in CI (which runs on Linux, where the bug doesn't reproduce). It's fixed and documented, but relies on "don't remove the UTF-8 reconfiguration at CLI startup" being understood by future editors of `cli/main.py`, not on a test failing if someone does.

---

## Summary: Principles behind the decisions

| Principle | How it shows up |
|---|---|
| **One code path per behavior, not one per interface** | CLI/API/Python library all call the same `Comparator`; no interface re-derives anomalies or severity counts |
| **Interfaces narrow enough to build in parallel** | `AbstractEngine`/`AbstractLayer`/`AbstractFormatter` each expose one method-shaped contract, fixed before any implementation started |
| **Explicit data flow over shared mutable state** | `column_mapping` is a parameter, not a context object; every layer's dependencies are visible in its signature |
| **Fail loud at the boundary, not deep inside a check** | Pydantic validation on every model; a crashing layer becomes one warning anomaly, not a stack trace |
| **Test isolation via injection, not mocking library internals** | `client_factory` for Portkey; every engine/formatter test runs against real Polars/DuckDB, never a mock of them |
| **Don't materialize what the engine choice was meant to avoid** | DuckDB frames stay SQL views; converting to Polars at read-time would defeat the point of picking DuckDB |
| **A green test suite is necessary, not sufficient** | The CLI was actually run against a realistic scenario, which is what caught the two real bugs unit tests didn't |
