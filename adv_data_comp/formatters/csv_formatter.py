from __future__ import annotations

import csv
import io
import json

from adv_data_comp.formatters.base import AbstractFormatter
from adv_data_comp.models import ComparisonResult

_FIELDNAMES = ["layer", "severity", "column", "message", "evidence", "explanation"]


class CsvFormatter(AbstractFormatter):
    """Renders a ComparisonResult as a flat CSV — one row per anomaly."""

    def format(self, result: ComparisonResult) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_FIELDNAMES)
        writer.writeheader()

        for anomaly in result.anomalies:
            writer.writerow(
                {
                    "layer": anomaly.layer,
                    "severity": anomaly.severity.value,
                    "column": anomaly.column,
                    "message": anomaly.message,
                    "evidence": json.dumps(anomaly.evidence),
                    "explanation": anomaly.explanation or "",
                }
            )

        return buffer.getvalue()
