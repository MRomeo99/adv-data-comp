from __future__ import annotations

import json

from adv_data_comp.formatters._shared import build_envelope
from adv_data_comp.formatters.base import AbstractFormatter
from adv_data_comp.models import ComparisonResult


class JsonFormatter(AbstractFormatter):
    """Renders a ComparisonResult as machine-readable JSON.

    Suitable for programmatic use in CI/CD pipelines (see CLAUDE.md's
    "Output formats" section). Uses the shared envelope builder in
    `_shared.py` so JSON and YAML output stay structurally identical.
    """

    def format(self, result: ComparisonResult) -> str:
        envelope = build_envelope(result)
        return json.dumps(envelope, indent=2)
