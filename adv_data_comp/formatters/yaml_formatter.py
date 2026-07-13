from __future__ import annotations

import yaml

from adv_data_comp.formatters._shared import build_envelope
from adv_data_comp.formatters.base import AbstractFormatter
from adv_data_comp.models import ComparisonResult


class YamlFormatter(AbstractFormatter):
    """Renders a ComparisonResult as YAML.

    Same logical structure as JsonFormatter (see CLAUDE.md's "Output
    formats" section: "Same structure as JSON but YAML"). Reuses the
    shared envelope builder in `_shared.py` to avoid duplicating the
    dict-building logic.
    """

    def format(self, result: ComparisonResult) -> str:
        envelope = build_envelope(result)
        return yaml.safe_dump(envelope, default_flow_style=False, sort_keys=False)
