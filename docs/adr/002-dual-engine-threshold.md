# ADR 002 — Dual engine with automatic threshold switching

## Context

Needed to handle both small files (fast in-memory) and large files (larger
than RAM) with identical output.

## Decision

Polars for combined file size ≤ 500MB, DuckDB for > 500MB. The threshold is
configurable (`--memory-threshold-mb` / `ADV_DATA_COMP_MEMORY_THRESHOLD_MB`).
Both engines implement the same `AbstractEngine` interface, so every
comparison layer calls engine methods and never imports Polars or DuckDB
directly.

## Why Polars for small files

Polars is faster than DuckDB for in-memory operations on small files. Its
lazy evaluation and columnar memory layout make profiling and statistical
comparison very fast.

## Why DuckDB for large files

DuckDB reads Parquet, CSV, and Excel directly from disk without loading into
memory, and handles files larger than available RAM via streaming execution.
For very large files, SQL-based aggregations (null counts, distinct counts,
min/max) are also more efficient than Polars scans.

## The threshold default (500MB)

Chosen because a 500MB file loaded as a Polars DataFrame typically consumes
~1.5–2GB RAM (type inference, index structures). Above this, DuckDB's
streaming is more reliable across different machine specs.

## Tradeoff

Two engines to maintain. Mitigated by the `AbstractEngine` interface —
adding a third engine (e.g. Spark for distributed files) requires only a
new implementation class.

## Swap path to production

For truly massive files (> 50GB), add a `SparkEngine` implementation behind
the same `AbstractEngine` interface — no layer code needs to change.

## Status

Implemented. See `adv_data_comp/engine/`. Every layer's test suite is
parametrized over both engines to enforce the identical-output guarantee.
