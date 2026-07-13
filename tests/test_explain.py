"""Tests for adv_data_comp.explain.portkey_explainer.explain_anomalies.

Follows the project's TDD philosophy: these tests were written before the
implementation (adv_data_comp/explain/portkey_explainer.py) existed, and were
confirmed to fail (module not found) before the implementation was added.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from adv_data_comp.explain.portkey_explainer import explain_anomalies
from adv_data_comp.models import Anomaly, Severity


def make_anomaly(
    layer: str, severity: Severity, column: str = "col", message: str = "msg"
) -> Anomaly:
    return Anomaly(layer=layer, severity=severity, column=column, message=message)


class FakeClient:
    """Hand-written fake standing in for the injected Portkey-like client.

    Contract: `.explain(anomalies_batch, prompt_id) -> dict[int, str]` maps the
    index of an anomaly *within the batch it was called with* to explanation text.
    """

    def __init__(self, responses: dict | None = None, raise_for_layers: set | None = None):
        self.responses = responses or {}
        self.raise_for_layers = raise_for_layers or set()
        self.calls: list[tuple[str | None, list[Anomaly], str]] = []

    def explain(self, anomalies_batch: list[Anomaly], prompt_id: str) -> dict[int, str]:
        layer = anomalies_batch[0].layer if anomalies_batch else None
        self.calls.append((layer, list(anomalies_batch), prompt_id))
        if layer in self.raise_for_layers:
            raise RuntimeError(f"boom for layer {layer}")
        return self.responses.get(
            layer,
            {i: f"explanation for {a.column}" for i, a in enumerate(anomalies_batch)},
        )


# (a) prompt_id=None -> anomalies unchanged, non-None warning mentioning the env var
def test_no_prompt_id_returns_unchanged_with_warning():
    anomalies = [make_anomaly("schema", Severity.CRITICAL)]

    result, warning = explain_anomalies(anomalies, prompt_id=None)

    assert result == anomalies
    assert result[0].explanation is None
    assert warning is not None
    assert "PORTKEY_PROMPT_EXPLAIN_ID" in warning


def test_falsy_prompt_id_empty_string_also_warns():
    anomalies = [make_anomaly("schema", Severity.WARNING)]

    result, warning = explain_anomalies(anomalies, prompt_id="")

    assert result[0].explanation is None
    assert warning is not None
    assert "PORTKEY_PROMPT_EXPLAIN_ID" in warning


# (e) empty anomaly list -> clean, no warning, no client_factory invocation at all
def test_empty_list_returns_cleanly_with_no_warning_and_no_factory_calls():
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return FakeClient()

    result, warning = explain_anomalies([], prompt_id="pp-123", client_factory=factory)

    assert result == []
    assert warning is None
    assert factory_calls == []


# Also verify this holds even when prompt_id is falsy (empty-list check should win)
def test_empty_list_with_no_prompt_id_still_no_warning():
    result, warning = explain_anomalies([], prompt_id=None)
    assert result == []
    assert warning is None


# (b) critical/warning get `.explanation` populated; info/suggestion do not
def test_critical_and_warning_get_explanations_info_and_suggestion_do_not():
    critical = make_anomaly("schema", Severity.CRITICAL, column="a")
    warning_anomaly = make_anomaly("schema", Severity.WARNING, column="b")
    info_anomaly = make_anomaly("schema", Severity.INFO, column="c")
    suggestion_anomaly = make_anomaly("schema", Severity.SUGGESTION, column="d")
    anomalies = [critical, warning_anomaly, info_anomaly, suggestion_anomaly]

    fake = FakeClient()
    result, warning = explain_anomalies(anomalies, prompt_id="pp-123", client_factory=lambda: fake)

    assert warning is None
    by_column = {a.column: a for a in result}
    assert by_column["a"].explanation is not None
    assert by_column["b"].explanation is not None
    assert by_column["c"].explanation is None
    assert by_column["d"].explanation is None

    # info/suggestion anomalies pass through untouched (identity preserved is fine either way,
    # but explanation must definitely remain None)
    assert by_column["c"] is info_anomaly or by_column["c"].explanation is None
    assert by_column["d"] is suggestion_anomaly or by_column["d"].explanation is None


def test_magicmock_client_factory_populates_explanation():
    anomalies = [make_anomaly("schema", Severity.CRITICAL, column="a")]
    mock_client = MagicMock()
    mock_client.explain.return_value = {0: "mocked explanation"}

    result, warning = explain_anomalies(
        anomalies, prompt_id="pp-123", client_factory=lambda: mock_client
    )

    assert result[0].explanation == "mocked explanation"
    mock_client.explain.assert_called_once()
    assert warning is None


# (c) batched per distinct layer: explain() called exactly once per distinct layer
# present among critical/warning anomalies (not once per anomaly, and layers that
# only contain info/suggestion anomalies never trigger a call at all).
def test_batched_exactly_once_per_distinct_layer():
    anomalies = [
        make_anomaly("schema", Severity.CRITICAL, column="a"),
        make_anomaly("schema", Severity.WARNING, column="b"),
        make_anomaly("statistical", Severity.CRITICAL, column="c"),
        make_anomaly("statistical", Severity.WARNING, column="d"),
        make_anomaly("statistical", Severity.INFO, column="e"),
        make_anomaly("format", Severity.SUGGESTION, column="f"),
    ]
    fake = FakeClient()

    result, warning = explain_anomalies(anomalies, prompt_id="pp-123", client_factory=lambda: fake)

    assert warning is None
    layers_called = [layer for layer, _, _ in fake.calls]
    assert layers_called.count("schema") == 1
    assert layers_called.count("statistical") == 1
    assert "format" not in layers_called  # only a suggestion anomaly there -> no call
    assert len(fake.calls) == 2

    schema_call = next(c for c in fake.calls if c[0] == "schema")
    assert len(schema_call[1]) == 2  # only the critical+warning schema anomalies

    statistical_call = next(c for c in fake.calls if c[0] == "statistical")
    assert len(statistical_call[1]) == 2  # critical+warning only, info excluded

    by_column = {a.column: a for a in result}
    assert by_column["a"].explanation is not None
    assert by_column["b"].explanation is not None
    assert by_column["c"].explanation is not None
    assert by_column["d"].explanation is not None
    assert by_column["e"].explanation is None  # info, untouched
    assert by_column["f"].explanation is None  # suggestion, untouched


# (d) one layer's call raises -> that layer stays unexplained + warning mentions it,
# other layers are unaffected and still get explained.
def test_one_layer_failure_does_not_block_other_layers():
    anomalies = [
        make_anomaly("schema", Severity.CRITICAL, column="a"),
        make_anomaly("statistical", Severity.WARNING, column="b"),
    ]
    fake = FakeClient(raise_for_layers={"schema"})

    result, warning = explain_anomalies(anomalies, prompt_id="pp-123", client_factory=lambda: fake)

    by_column = {a.column: a for a in result}
    assert by_column["a"].explanation is None
    assert by_column["b"].explanation is not None
    assert warning is not None
    assert "schema" in warning


def test_multiple_layer_failures_all_noted_and_do_not_raise():
    anomalies = [
        make_anomaly("schema", Severity.CRITICAL, column="a"),
        make_anomaly("statistical", Severity.WARNING, column="b"),
        make_anomaly("semantic", Severity.CRITICAL, column="c"),
    ]
    fake = FakeClient(raise_for_layers={"schema", "statistical"})

    result, warning = explain_anomalies(anomalies, prompt_id="pp-123", client_factory=lambda: fake)

    by_column = {a.column: a for a in result}
    assert by_column["a"].explanation is None
    assert by_column["b"].explanation is None
    assert by_column["c"].explanation is not None
    assert warning is not None
    assert "schema" in warning
    assert "statistical" in warning
