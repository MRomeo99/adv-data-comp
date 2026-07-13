from __future__ import annotations

from pathlib import Path

from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.models import ColumnProfile


class PolarsEngine(AbstractEngine):
    """In-memory engine for small/medium files (combined size <= threshold)."""

    def read(self, path: Path) -> EngineFrame:
        raise NotImplementedError("PolarsEngine.read is implemented in a later slice")

    def profile_column(self, frame: EngineFrame, column: str) -> ColumnProfile:
        raise NotImplementedError("PolarsEngine.profile_column is implemented in a later slice")

    def row_count(self, frame: EngineFrame) -> int:
        raise NotImplementedError("PolarsEngine.row_count is implemented in a later slice")

    def find_missing_keys(
        self, frame_a: EngineFrame, frame_b: EngineFrame, key: str
    ) -> EngineFrame:
        raise NotImplementedError(
            "PolarsEngine.find_missing_keys is implemented in a later slice"
        )
