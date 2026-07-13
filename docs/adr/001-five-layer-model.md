# ADR 001 — Five-layer comparison model

## Context

Existing tools compare files at one layer only (schema diff, or row diff,
or value diff). Real data quality issues span multiple layers simultaneously
and require different detection logic per layer.

## Decision

Five sequential layers — format, schema, semantic, statistical, referential.
Each layer is independent and produces typed `Anomaly` objects. Layers can
be run selectively via the `--layers` flag.

## Tradeoff

More complex than a single-pass comparison. Mitigated by the layer
abstraction (`AbstractLayer`) — each layer is independently testable and
replaceable.

## Status

Implemented. See `adv_data_comp/layers/`.
