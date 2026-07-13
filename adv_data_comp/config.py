from __future__ import annotations

import os
from enum import Enum

from pydantic import BaseModel, Field

from adv_data_comp.models import Layer


def _default_memory_threshold_mb() -> float:
    raw = os.environ.get("ADV_DATA_COMP_MEMORY_THRESHOLD_MB")
    return float(raw) if raw else 500.0


class OutputFormat(str, Enum):
    HTML = "html"
    JSON = "json"
    YAML = "yaml"
    MARKDOWN = "markdown"
    CSV = "csv"
    DBT = "dbt"


class ComparisonConfig(BaseModel):
    key: str | None = None
    layers: list[Layer] = Field(
        default_factory=lambda: ["format", "schema", "semantic", "statistical", "referential"]
    )
    fuzzy_threshold: float = 0.80
    memory_threshold_mb: float = Field(default_factory=_default_memory_threshold_mb)
    explain: bool = False
    output_formats: list[OutputFormat] = Field(default_factory=list)
    output_dir: str = "./"
    severity_filter: list[str] | None = None
    sheet: str | None = None
