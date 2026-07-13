from __future__ import annotations

from abc import ABC, abstractmethod

from adv_data_comp.models import ComparisonResult


class AbstractFormatter(ABC):
    """Renders a ComparisonResult into one output format.

    Every formatter is generated from the same ComparisonResult object —
    formatters must never recompute anomalies, only render them.
    """

    @abstractmethod
    def format(self, result: ComparisonResult) -> str:
        """Returns the fully rendered report as a string."""
        ...
