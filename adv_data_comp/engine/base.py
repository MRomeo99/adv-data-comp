from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from adv_data_comp.models import ColumnProfile

EngineFrame = Any


class AbstractEngine(ABC):
    """Common interface implemented by every comparison engine (Polars, DuckDB, ...).

    Comparison layers only ever call methods on this interface — never Polars
    or DuckDB directly — so a new engine (e.g. Spark) can be added without
    touching layer code.
    """

    @abstractmethod
    def read(self, path: Path) -> EngineFrame: ...

    @abstractmethod
    def profile_column(self, frame: EngineFrame, column: str) -> ColumnProfile: ...

    @abstractmethod
    def row_count(self, frame: EngineFrame) -> int: ...

    @abstractmethod
    def find_missing_keys(
        self, frame_a: EngineFrame, frame_b: EngineFrame, key: str
    ) -> EngineFrame: ...
