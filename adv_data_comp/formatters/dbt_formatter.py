from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from adv_data_comp.formatters.base import AbstractFormatter
from adv_data_comp.models import Anomaly, ComparisonResult

_PLACEHOLDER_MODEL_NAME = "compared_model"


class DbtFormatter(AbstractFormatter):
    """Renders a ComparisonResult as a dbt `schema.yml` fragment.

    Per ADR 004 ("dbt schema.yml as an output format"), this is a
    deliberately HEURISTIC, best-effort MVP: it derives a small set of dbt
    generic tests from anomalies using simple, documented rules below. It
    is NOT exhaustive and does not attempt to cover every anomaly type or
    every dbt test dbt-utils offers. Per the ADR's own tradeoff note, the
    generated YAML is "opinionated... always review before committing."

    Rule -> dbt test mapping implemented:

    1. `not_null` — for a `layer="statistical"` anomaly whose evidence has
       both `null_rate_a` and `null_rate_b` (see statistical_layer.py),
       where `null_rate_a < null_rate_b` (file A, the reference/ground
       truth, has fewer nulls than file B). This converts "file B
       introduced more nulls than the reference" into a permanent guard.
       Anomalies where A's null rate is >= B's are intentionally skipped —
       guarding against a direction that didn't regress would be a
       meaningless test.

    2. `unique` — for a `layer="referential"` duplicate-key anomaly whose
       evidence shows `{"file": "a", "duplicate_count": <n>}` (see
       referential_layer.py's `_duplicate_keys`), i.e. duplicates found in
       file A specifically. Duplicates found only in file B are skipped:
       B being messier than the reference doesn't tell us the reference
       column is meant to be unique.

    Rules intentionally SKIPPED (not implemented), per the task spec:

    3. `relationships` — referential "missing rows" anomalies (rows in A
       not in B / evidence `missing_count`) do not map to a single-file
       dbt test: a `relationships` test needs a parent model reference
       this tool has no way to know. Fabricating one would be misleading,
       so these anomalies produce no test entry at all.

    4. `accepted_values` / `dbt_utils.*` — explicitly optional per the
       task spec. There's no clean, general signal in the current schema/
       statistical anomaly evidence (e.g. a reliable boolean-category
       flag) to derive this without guessing, so it's skipped rather than
       forcing a low-confidence heuristic.

    Any other anomaly (format layer, semantic layer, schema type-change,
    schema nullability, etc.) has no mapping rule and produces no test —
    this is intentional, not an oversight (see test coverage for the "no
    spurious test" case).

    Design choice on the illustrative `# null rate in source was 2.1%...`
    inline YAML comment shown in CLAUDE.md's ADR-004 example: PyYAML's
    `safe_dump` cannot easily emit inline comments next to list items
    without a custom representer/dumper. Rather than fight the library for
    cosmetic parity with the illustrative example, this formatter emits a
    clean test list (plain strings, e.g. `not_null`) and drops the inline
    "why" annotation. The rationale for each generated test is fully
    documented here in code instead. This keeps the output valid,
    unsurprising dbt YAML — which the task explicitly said matters more
    than mimicking the inline-comment example verbatim.
    """

    def format(self, result: ComparisonResult) -> str:
        model_name = self._derive_model_name(result)
        columns = self._build_columns(result.anomalies)

        model: dict[str, Any] = {"name": model_name, "columns": columns}
        envelope = {"models": [model]}

        return yaml.safe_dump(envelope, sort_keys=False, default_flow_style=False)

    @staticmethod
    def _derive_model_name(result: ComparisonResult) -> str:
        if result.meta is None:
            return _PLACEHOLDER_MODEL_NAME
        return Path(result.meta.file_a.path).stem

    @staticmethod
    def _column_name(anomaly_column: str) -> str:
        # Statistical-layer anomalies label fuzzy-matched columns as
        # "name_a~name_b" (see StatisticalLayer._column_label). The dbt
        # model is generated from file A's structure, so use the A side.
        if "~" in anomaly_column:
            return anomaly_column.split("~", 1)[0]
        return anomaly_column

    def _build_columns(self, anomalies: list[Anomaly]) -> list[dict[str, Any]]:
        tests_by_column: dict[str, list[str]] = {}

        for anomaly in anomalies:
            test_name = self._map_anomaly_to_test(anomaly)
            if test_name is None:
                continue
            column = self._column_name(anomaly.column)
            existing = tests_by_column.setdefault(column, [])
            if test_name not in existing:
                existing.append(test_name)

        return [
            {"name": column, "tests": tests}
            for column, tests in tests_by_column.items()
        ]

    @staticmethod
    def _map_anomaly_to_test(anomaly: Anomaly) -> str | None:
        evidence = anomaly.evidence

        # Rule 1: not_null
        if anomaly.layer == "statistical" and (
            "null_rate_a" in evidence and "null_rate_b" in evidence
        ):
            if evidence["null_rate_a"] < evidence["null_rate_b"]:
                return "not_null"
            return None

        # Rule 2: unique
        if anomaly.layer == "referential" and (
            evidence.get("file") == "a" and "duplicate_count" in evidence
        ):
            return "unique"

        # Rules 3 (relationships) and 4 (accepted_values) intentionally
        # skipped -- see class docstring.
        return None
