from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Layer = Literal["format", "schema", "semantic", "statistical", "referential"]


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    SUGGESTION = "suggestion"


class Anomaly(BaseModel):
    layer: Layer
    severity: Severity
    column: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    explanation: str | None = None


class FileMeta(BaseModel):
    path: str
    format: str
    rows: int
    size_mb: float


class ComparisonMeta(BaseModel):
    comparison_id: str
    file_a: FileMeta
    file_b: FileMeta
    engine: str
    layers_run: list[Layer]
    runtime_seconds: float


class ComparisonResult(BaseModel):
    anomalies: list[Anomaly] = Field(default_factory=list)
    meta: ComparisonMeta | None = None

    @property
    def summary(self) -> dict[str, int]:
        counts = {severity.value: 0 for severity in Severity}
        for anomaly in self.anomalies:
            counts[anomaly.severity.value] += 1
        return counts

    @property
    def critical(self) -> list[Anomaly]:
        return [a for a in self.anomalies if a.severity == Severity.CRITICAL]

    @property
    def has_critical(self) -> bool:
        return len(self.critical) > 0

    @property
    def schema_match(self) -> bool:
        return not any(a.layer == "schema" for a in self.anomalies)


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    null_count: int = 0
    row_count: int = 0
    distinct_count: int | None = None
    min_value: Any = None
    max_value: Any = None
    mean: float | None = None
    stddev: float | None = None

    @property
    def null_rate(self) -> float:
        if self.row_count == 0:
            return 0.0
        return self.null_count / self.row_count
