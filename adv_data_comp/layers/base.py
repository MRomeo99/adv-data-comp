from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.models import Anomaly, Layer


class AbstractLayer(ABC):
    """One of the five sequential comparison layers.

    Layers are independent — a failure in one layer must not prevent later
    layers from running. The Comparator orchestrator is responsible for
    catching per-layer exceptions and continuing.
    """

    layer_name: ClassVar[Layer]

    @abstractmethod
    def compare(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        config: ComparisonConfig,
        column_mapping: dict[str, str] | None = None,
    ) -> list[Anomaly]:
        """Compare frame_a (reference) against frame_b (new/actual).

        column_mapping optionally maps a column name in file B to its matched
        column name in file A (identity for exact matches, fuzzy suggestions
        from the semantic layer otherwise). Layers that don't need column
        matching (format, schema, semantic) may ignore it.
        """
        ...
