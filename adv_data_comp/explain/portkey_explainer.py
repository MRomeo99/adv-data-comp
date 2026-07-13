"""LLM-generated anomaly explanations via Portkey (`--explain`, opt-in).

Only invoked when the caller explicitly asks for explanations. Nothing in
this module talks to Portkey, reads Portkey/LLM env vars, or imports
``portkey_ai`` unless :func:`explain_anomalies` actually needs to make a
call -- i.e. there is at least one anomaly to explain AND a prompt_id was
supplied. This keeps the "zero API keys required" guarantee for every code
path that doesn't use ``--explain``.

Client contract
----------------
``client_factory`` is a zero-argument callable returning an object with:

    def explain(self, anomalies_batch: list[Anomaly], prompt_id: str) -> dict[int, str]:
        ...

``anomalies_batch`` is the list of critical/warning :class:`Anomaly` objects
for ONE layer, in stable input order. The returned dict maps the *index of
the anomaly within that batch* (0-based, matching ``anomalies_batch``'s
order) to the explanation text for that anomaly. Using the batch-relative
index (rather than e.g. column name) avoids ambiguity when two anomalies in
the same batch share a column. Any index missing from the returned dict is
simply left unexplained; the same is true for falsy explanation strings.

If ``client_factory`` is not supplied, :func:`_default_client_factory` is
used -- this is the only place the real ``portkey_ai`` SDK is imported and
the only place Portkey-related env vars are read, and it is never reached
by the test suite (tests always inject a fake factory).
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Protocol

from adv_data_comp.models import Anomaly, Severity

# Only these severities are ever sent for explanation.
_EXPLAINABLE_SEVERITIES = frozenset({Severity.CRITICAL, Severity.WARNING})

_MISSING_PROMPT_ID_WARNING = (
    "PORTKEY_PROMPT_EXPLAIN_ID is not set; skipping LLM explanations for "
    "critical/warning anomalies. Set the PORTKEY_PROMPT_EXPLAIN_ID environment "
    "variable to a Portkey Prompt Library prompt ID (e.g. 'pp-...') to enable "
    "--explain."
)


class ExplainerClient(Protocol):
    """Structural contract expected of objects returned by `client_factory()`."""

    def explain(self, anomalies_batch: list[Anomaly], prompt_id: str) -> dict[int, str]: ...


def explain_anomalies(
    anomalies: list[Anomaly],
    prompt_id: str | None,
    client_factory: Callable[[], Any] | None = None,
) -> tuple[list[Anomaly], str | None]:
    """Fill in `.explanation` for critical/warning anomalies via Portkey.

    Returns (anomalies_with_explanations_filled_in, warning_message_or_None).

    Batching: anomalies are grouped by `.layer` (only critical/warning ones
    are considered), and exactly one call to the client's `.explain()` is
    made per distinct layer present. info/suggestion anomalies are always
    returned unchanged. If a given layer's call raises, that layer's
    anomalies are left unexplained, a warning is recorded noting which
    layer(s) failed, and other layers are unaffected. This function never
    raises on account of the LLM call itself.
    """
    if not anomalies:
        return list(anomalies), None

    if not prompt_id:
        return list(anomalies), _MISSING_PROMPT_ID_WARNING

    # Group indices of explainable (critical/warning) anomalies by layer,
    # preserving input order within each layer's batch.
    layer_batches: dict[str, list[int]] = {}
    for idx, anomaly in enumerate(anomalies):
        if anomaly.severity in _EXPLAINABLE_SEVERITIES:
            layer_batches.setdefault(anomaly.layer, []).append(idx)

    result = list(anomalies)

    if not layer_batches:
        return result, None

    factory = client_factory or _default_client_factory
    client = factory()

    failed_layers: list[str] = []
    for layer, indices in layer_batches.items():
        batch = [anomalies[i] for i in indices]
        try:
            explanations = client.explain(batch, prompt_id)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, never fail the caller
            failed_layers.append(f"{layer} ({exc})")
            continue

        for batch_idx, original_idx in enumerate(indices):
            text = explanations.get(batch_idx) if explanations else None
            if text:
                result[original_idx] = anomalies[original_idx].model_copy(
                    update={"explanation": text}
                )

    warning: str | None = None
    if failed_layers:
        warning = (
            "Failed to get LLM explanations for layer(s): "
            + ", ".join(failed_layers)
            + ". Those anomalies were left unexplained."
        )

    return result, warning


def _default_client_factory() -> "PortkeyExplainerClient":
    """Build the real Portkey-backed client. Only called when --explain is used
    AND no client_factory was injected (i.e. never in tests)."""
    return PortkeyExplainerClient()


class PortkeyExplainerClient:
    """Thin wrapper around the real `portkey_ai` SDK.

    ASSUMPTIONS (no network access was available to verify the exact SDK
    call shape against a live Portkey account -- these are a best-effort
    based on the installed `portkey_ai` package's public signatures and
    typical LLM client SDK conventions; adjust if Portkey's actual response
    shape differs):

    - `Portkey(api_key=..., config=...)` constructs a client. `api_key` is
      read from the `PORTKEY_API_KEY` env var.
    - Model routing/fallback is expressed via Portkey's `config` object
      rather than an SDK method: a `strategy: {mode: "fallback"}` with an
      ordered `targets` list. We configure `gemini-2.5-flash` as the primary
      target and `gpt-4o-mini` as the fallback target, per CLAUDE.md. This
      keeps model selection as a routing/config concern rather than
      something hardcoded into call sites, and is the standard Portkey
      pattern for fallback behavior.
    - A saved Prompt Library prompt is invoked via
      `client.prompts.completions.create(prompt_id=..., variables={...})`
      (confirmed against the installed SDK's method signature).
    - We pass the anomaly batch to the prompt template as a `variables`
      mapping (`anomalies_json`), assuming the Portkey Prompt Library
      template (id referenced by `PORTKEY_PROMPT_EXPLAIN_ID`,
      `adv-data-comp-explain`) is authored to accept that variable and to
      return a JSON object mapping batch-relative index (as a string) to
      explanation text, e.g. `{"0": "...", "1": "..."}`. This keeps the
      prompt template itself in charge of the exact wording/format
      contract, while this wrapper only needs to parse a JSON object back
      out of the completion text.
    - The completion text is read from `response.choices[0].message.content`
      (confirmed as the response shape via the installed SDK's
      `PromptCompletion`/`Choice` models), falling back to `response.text`
      if `.choices` is absent (some Portkey routes return a bare `text`
      completion instead of a chat-style one).
    """

    def __init__(self) -> None:
        # Imported lazily so `portkey_ai` and Portkey env vars are only ever
        # touched when this real client is actually instantiated (i.e. never
        # during --explain-free usage, and never in tests, which inject a
        # fake client_factory).
        from portkey_ai import Portkey

        api_key = os.environ.get("PORTKEY_API_KEY")

        # Best-effort fallback routing config: try gemini-2.5-flash first,
        # fall back to gpt-4o-mini if the primary target errors out. See
        # class docstring for the caveat that the exact Portkey config
        # schema wasn't verified against a live account/network.
        config = {
            "strategy": {"mode": "fallback"},
            "targets": [
                {"override_params": {"model": "gemini-2.5-flash"}},
                {"override_params": {"model": "gpt-4o-mini"}},
            ],
        }

        self._client = Portkey(api_key=api_key, config=config)

    def explain(self, anomalies_batch: list[Anomaly], prompt_id: str) -> dict[int, str]:
        variables = {
            "anomalies_json": json.dumps([a.model_dump(mode="json") for a in anomalies_batch]),
        }

        response = self._client.prompts.completions.create(
            prompt_id=prompt_id,
            variables=variables,
        )

        text: str | None = None
        choices = getattr(response, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            text = getattr(message, "content", None) if message is not None else None
        if not text:
            text = getattr(response, "text", None)

        if not text:
            return {}

        parsed = json.loads(text)
        return {int(k): v for k, v in parsed.items()}
