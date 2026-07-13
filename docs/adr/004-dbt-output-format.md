# ADR 004 — dbt schema.yml as an output format

## Context

A one-time comparison is valuable; permanent pipeline protection is more
valuable. Converting comparison findings into dbt tests means the anomalies
found today become guards against future regressions.

## Decision

`--report dbt` generates a `schema.yml` fragment with dbt generic tests
derived from the anomalies found, treating file A as the reference/"ground
truth":

- A `not_null` test when a statistical-layer anomaly shows file B's null
  rate exceeding file A's for that column.
- A `unique` test when a referential-layer anomaly shows duplicate key
  values within file A.

`relationships` tests are deliberately not generated — they require a
parent-model reference this tool has no way to infer from a two-file
comparison, and a fabricated reference would be worse than no test at all.

## Tradeoff

The generated dbt YAML is opinionated and may not match the user's existing
dbt project structure exactly. Always review before committing.

## Status

Implemented (MVP). See `adv_data_comp/formatters/dbt_formatter.py`. The
not_null/unique mapping covers the two anomaly types with the clearest,
least-ambiguous test equivalent; broader mappings (e.g. `accepted_values`)
are left for a future iteration once there's a reliable signal to derive
them from.
