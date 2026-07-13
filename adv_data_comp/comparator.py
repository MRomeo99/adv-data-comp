from __future__ import annotations

import time
import uuid
from pathlib import Path

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.selector import select_engine
from adv_data_comp.layers.base import AbstractLayer
from adv_data_comp.layers.format_layer import FormatLayer
from adv_data_comp.layers.referential_layer import ReferentialLayer
from adv_data_comp.layers.schema_layer import SchemaLayer
from adv_data_comp.layers.semantic_layer import SemanticLayer
from adv_data_comp.layers.statistical_layer import StatisticalLayer
from adv_data_comp.models import Anomaly, ComparisonMeta, ComparisonResult, FileMeta, Severity

# Layers whose checks depend on knowing which columns the semantic layer
# fuzzy-matched (so they can compare beyond exact-name matches).
_COLUMN_MAPPING_CONSUMERS = {"statistical", "referential"}


class Comparator:
    def __init__(self, config: ComparisonConfig | None = None) -> None:
        self.config = config or ComparisonConfig()

    def _build_layer(self, name: str, path_a: Path, path_b: Path) -> AbstractLayer:
        if name == "format":
            return FormatLayer(path_a, path_b)
        if name == "schema":
            return SchemaLayer()
        if name == "semantic":
            return SemanticLayer()
        if name == "statistical":
            return StatisticalLayer()
        if name == "referential":
            return ReferentialLayer()
        raise ValueError(f"Unknown layer: {name}")

    def compare(self, file_a: str | Path, file_b: str | Path) -> ComparisonResult:
        path_a, path_b = Path(file_a), Path(file_b)
        start = time.perf_counter()

        engine = select_engine(path_a, path_b, self.config.memory_threshold_mb)
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        anomalies: list[Anomaly] = []
        layers_run: list[str] = []
        column_mapping: dict[str, str] = {}

        for layer_name in self.config.layers:
            layer = self._build_layer(layer_name, path_a, path_b)
            mapping_arg = column_mapping if layer_name in _COLUMN_MAPPING_CONSUMERS else None

            try:
                layer_anomalies = layer.compare(engine, frame_a, frame_b, self.config, mapping_arg)
            except Exception as exc:  # noqa: BLE001 - layers must never abort the run
                layer_anomalies = [
                    Anomaly(
                        layer=layer_name,
                        severity=Severity.WARNING,
                        column="__file__",
                        message=f"Layer '{layer_name}' failed: {exc}",
                        evidence={"error": str(exc), "error_type": type(exc).__name__},
                    )
                ]

            anomalies.extend(layer_anomalies)
            layers_run.append(layer_name)

            if layer_name == "semantic":
                for anomaly in layer_anomalies:
                    if "suggested_mapping" in anomaly.evidence:
                        column_mapping[anomaly.evidence["suggested_mapping"]] = anomaly.column

        if self.config.severity_filter:
            allowed = set(self.config.severity_filter)
            anomalies = [a for a in anomalies if a.severity.value in allowed]

        runtime_seconds = time.perf_counter() - start
        meta = ComparisonMeta(
            comparison_id=str(uuid.uuid4()),
            file_a=_file_meta(engine, frame_a, path_a),
            file_b=_file_meta(engine, frame_b, path_b),
            engine=type(engine).__name__.removesuffix("Engine").lower(),
            layers_run=layers_run,
            runtime_seconds=runtime_seconds,
        )
        return ComparisonResult(anomalies=anomalies, meta=meta)


def _file_meta(engine, frame, path: Path) -> FileMeta:
    return FileMeta(
        path=str(path),
        format=path.suffix.lower().lstrip("."),
        rows=engine.row_count(frame),
        size_mb=path.stat().st_size / 1_048_576,
    )
