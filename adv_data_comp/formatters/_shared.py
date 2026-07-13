from __future__ import annotations

from typing import Any

from adv_data_comp.models import ComparisonResult


def build_envelope(result: ComparisonResult) -> dict[str, Any]:
    """Build the shared plain-dict envelope used by JSON and YAML formatters.

    Mirrors the outer shape shown in CLAUDE.md's "Output formats" JSON
    example (comparison_id, file_a, file_b, engine, layers_run, anomalies,
    summary, runtime_seconds) — flat at the top level, not nested under a
    "meta" key.

    Anomalies are serialized with their real model fields (layer, severity,
    column, message, evidence, explanation) rather than the illustrative
    flattened stat_name/value_a/value_b/delta shape from that example, since
    the actual Anomaly model carries a freeform `evidence` dict whose keys
    vary per layer/check.

    Design choice for `result.meta is None`: rather than introducing a
    nested "meta": null key (which would break the flat envelope shape),
    the meta-derived top-level fields (comparison_id, file_a, file_b,
    engine, layers_run, runtime_seconds) are set to None individually.
    `anomalies` and `summary` are always populated from the result itself,
    since they don't depend on meta.
    """
    meta = result.meta

    if meta is not None:
        comparison_id: Any = meta.comparison_id
        file_a: Any = meta.file_a.model_dump(mode="json")
        file_b: Any = meta.file_b.model_dump(mode="json")
        engine: Any = meta.engine
        layers_run: Any = list(meta.layers_run)
        runtime_seconds: Any = meta.runtime_seconds
    else:
        comparison_id = None
        file_a = None
        file_b = None
        engine = None
        layers_run = None
        runtime_seconds = None

    anomalies = [anomaly.model_dump(mode="json") for anomaly in result.anomalies]

    return {
        "comparison_id": comparison_id,
        "file_a": file_a,
        "file_b": file_b,
        "engine": engine,
        "layers_run": layers_run,
        "anomalies": anomalies,
        "summary": result.summary,
        "runtime_seconds": runtime_seconds,
    }
