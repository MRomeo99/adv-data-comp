from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar

import Levenshtein

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.base import AbstractEngine, EngineFrame
from adv_data_comp.engine.duckdb_engine import DuckDBFrame
from adv_data_comp.layers.base import AbstractLayer
from adv_data_comp.models import Anomaly, Layer, Severity

# --- name normalization / tokenization ------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")

# Levenshtein ratio threshold above which two individual tokens are treated
# as "the same concept" for the fuzzy token-similarity calculation below
# (e.g. "cust" vs "customer" -> ratio ~0.667). Chosen with margin above the
# 0.6 ratio for a clearly-unrelated pair like "name"/"number" so it doesn't
# false-positive.
_TOKEN_FUZZY_MATCH_THRESHOLD = 0.65

_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y"]

_UNIT_SUFFIXES = sorted(
    ["_usd", "_eur", "_gbp", "_pct", "_percent", "_kg", "_lb", "_lbs", "_cm", "_m", "_ft"],
    key=len,
    reverse=True,
)

# Generic/filler tokens that carry little semantic weight on their own (used
# by the duplicate-semantic-column heuristic, check 4).
_GENERIC_TOKENS = {"name", "id", "value", "date"}


def _normalize_name(name: str) -> str:
    """Lowercase and strip all non-alphanumeric characters."""
    return _NON_ALNUM_RE.sub("", name.lower())


def _tokenize(name: str) -> set[str]:
    """Split a column name into lowercase tokens on underscores, other
    non-alphanumeric separators, and camelCase boundaries.

    "customer_first_name" -> {"customer", "first", "name"}
    "firstName"           -> {"first", "name"}
    "CustomerID"          -> {"customer", "id"}
    """
    spaced = _CAMEL_BOUNDARY_RE.sub("_", name)
    parts = _TOKEN_SPLIT_RE.split(spaced)
    return {p.lower() for p in parts if p}


def _token_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Jaccard-like similarity over token sets, with fuzzy token equivalence.

    A strict Jaccard over *exact* token strings cannot reconcile the ADR-003
    worked example ("CustomerID" / "customer_id" / "cust_id" / "CUST_ID" must
    all score >= 0.80 against each other): "cust" and "customer" are
    different tokens by exact-string comparison. To satisfy that requirement
    while staying true to the spirit of "token-level Jaccard", two tokens
    are treated as a match if they are identical OR their own Levenshtein
    ratio clears _TOKEN_FUZZY_MATCH_THRESHOLD (covers common abbreviations).
    Matching is greedy (each token used at most once), and when all matches
    happen to be exact this reduces to standard Jaccard.
    """
    if not tokens_a and not tokens_b:
        return 0.0

    remaining_b = set(tokens_b)
    matched = 0
    for token_a in tokens_a:
        if token_a in remaining_b:
            remaining_b.remove(token_a)
            matched += 1
            continue
        best_match: str | None = None
        best_ratio = 0.0
        for token_b in remaining_b:
            ratio = Levenshtein.ratio(token_a, token_b)
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = token_b
        if best_match is not None and best_ratio >= _TOKEN_FUZZY_MATCH_THRESHOLD:
            remaining_b.remove(best_match)
            matched += 1

    union_size = len(tokens_a) + len(tokens_b) - matched
    if union_size <= 0:
        return 0.0
    return matched / union_size


def _similarity_score(name_a: str, name_b: str) -> float:
    """Combined fuzzy-match score per ADR-003: 60% normalized Levenshtein
    ratio + 40% token-level (fuzzy) Jaccard similarity."""
    levenshtein_ratio = Levenshtein.ratio(_normalize_name(name_a), _normalize_name(name_b))
    jaccard = _token_similarity(_tokenize(name_a), _tokenize(name_b))
    return 0.6 * levenshtein_ratio + 0.4 * jaccard


# --- type coercion helpers --------------------------------------------------


def _is_int(value: str) -> bool:
    try:
        int(value)
        return True
    except ValueError:
        return False


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _is_date(value: str) -> bool:
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def _detect_coercion_type(samples: list) -> str | None:
    if not samples:
        return None
    str_samples = [str(s) for s in samples]
    if all(_is_int(s) for s in str_samples):
        return "int"
    if all(_is_float(s) for s in str_samples):
        return "float"
    if all(_is_date(s) for s in str_samples):
        return "date"
    return None


def _sample_values(frame: EngineFrame, column: str) -> list:
    """Pull up to 20 non-null sample values from a column, per-engine.

    This is deliberately engine-specific (rather than routed through
    AbstractEngine) because sample extraction is only needed by this one
    semantic check; see the semantic_layer task spec for the exact per-engine
    incantations.
    """
    if isinstance(frame, DuckDBFrame):
        rows = frame.con.sql(
            f'SELECT "{column}" FROM {frame.view_name} '
            f'WHERE "{column}" IS NOT NULL LIMIT 20'
        ).fetchall()
        return [row[0] for row in rows]
    # Polars frame.
    return frame[column].drop_nulls().head(20).to_list()


# --- unit suffix helper ------------------------------------------------------


def _strip_unit_suffix(name: str) -> str:
    lowered = name.lower()
    for suffix in _UNIT_SUFFIXES:
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


class SemanticLayer(AbstractLayer):
    """Layer 3 — finds columns that mean the same thing but are named or
    typed differently. The only layer that does fuzzy name matching; it
    operates exclusively on columns that were NOT already exactly matched by
    name between the two files (exact matches are the schema layer's job).
    """

    layer_name: ClassVar[Layer] = "semantic"

    def compare(
        self,
        engine: AbstractEngine,
        frame_a: EngineFrame,
        frame_b: EngineFrame,
        config: ComparisonConfig,
        column_mapping: dict[str, str] | None = None,
    ) -> list[Anomaly]:
        schema_a = engine.schema(frame_a)
        schema_b = engine.schema(frame_b)

        unmatched_a = [name for name in schema_a if name not in schema_b]
        unmatched_b = [name for name in schema_b if name not in schema_a]

        anomalies: list[Anomaly] = []
        anomalies.extend(self._fuzzy_name_matches(unmatched_a, unmatched_b, config))
        anomalies.extend(
            self._type_coercion_candidates(frame_a, unmatched_a, schema_a)
        )
        anomalies.extend(self._unit_difference_candidates(unmatched_a, unmatched_b))
        anomalies.extend(self._duplicate_semantic_groups(unmatched_a, unmatched_b))
        return anomalies

    # -- check 1: fuzzy name matching ---------------------------------------

    def _fuzzy_name_matches(
        self,
        unmatched_a: list[str],
        unmatched_b: list[str],
        config: ComparisonConfig,
    ) -> list[Anomaly]:
        candidates: list[tuple[float, str, str]] = []
        for column_a in unmatched_a:
            for column_b in unmatched_b:
                score = _similarity_score(column_a, column_b)
                if score >= config.fuzzy_threshold:
                    candidates.append((score, column_a, column_b))

        # Greedy bipartite matching: highest-scoring pairs win first; a
        # column already claimed (on either side) cannot be reused.
        candidates.sort(key=lambda c: c[0], reverse=True)

        claimed_a: set[str] = set()
        claimed_b: set[str] = set()
        anomalies: list[Anomaly] = []
        for score, column_a, column_b in candidates:
            if column_a in claimed_a or column_b in claimed_b:
                continue
            claimed_a.add(column_a)
            claimed_b.add(column_b)
            anomalies.append(
                Anomaly(
                    layer="semantic",
                    severity=Severity.SUGGESTION,
                    column=column_a,
                    message=(
                        f"Possible column match: '{column_a}' (file A) <-> "
                        f"'{column_b}' (file B), similarity {score:.2f}"
                    ),
                    evidence={
                        "column_a": column_a,
                        "column_b": column_b,
                        "similarity_score": score,
                        "suggested_mapping": column_b,
                    },
                )
            )
        return anomalies

    # -- check 2: type coercion candidates -----------------------------------

    def _type_coercion_candidates(
        self,
        frame_a: EngineFrame,
        unmatched_a: list[str],
        schema_a,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        for column_a in unmatched_a:
            if schema_a[column_a].category != "string":
                continue
            samples = _sample_values(frame_a, column_a)
            candidate_type = _detect_coercion_type(samples)
            if candidate_type is None:
                continue
            anomalies.append(
                Anomaly(
                    layer="semantic",
                    severity=Severity.SUGGESTION,
                    column=column_a,
                    message=(
                        f"Column '{column_a}' is stored as string but all sampled "
                        f"values parse cleanly as {candidate_type}; consider "
                        f"coercing its type."
                    ),
                    evidence={"candidate_type": candidate_type},
                )
            )
        return anomalies

    # -- check 3: unit difference detection -----------------------------------

    def _unit_difference_candidates(
        self, unmatched_a: list[str], unmatched_b: list[str]
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        for column_a in unmatched_a:
            stripped_a = _strip_unit_suffix(column_a)
            if not stripped_a:
                continue
            for column_b in unmatched_b:
                stripped_b = _strip_unit_suffix(column_b)
                if stripped_a == stripped_b:
                    anomalies.append(
                        Anomaly(
                            layer="semantic",
                            severity=Severity.SUGGESTION,
                            column=column_a,
                            message=(
                                f"'{column_a}' (file A) and '{column_b}' (file B) share "
                                f"the same base name but differ by a unit suffix; "
                                f"possible unit mismatch."
                            ),
                            evidence={
                                "column_a": column_a,
                                "column_b": column_b,
                                "note": "possible unit difference, verify manually",
                            },
                        )
                    )
        return anomalies

    # -- check 4: duplicate semantic columns (best-effort heuristic) --------
    #
    # Heuristic: a column_a name is treated as "generic" if, after removing
    # filler tokens (name/id/value/date), at most one non-generic token
    # remains (e.g. "full_name" -> {"full"}) AND it still contains at least
    # one filler/generic token (e.g. "name"). If two or more unmatched
    # columns in file B share that same generic token in their own token
    # sets (e.g. "first_name" and "last_name" both contain "name"), we
    # suspect column_a may have been split into those columns in file B and
    # emit a single grouped suggestion. This is intentionally conservative
    # (requires >= 2 candidates) and best-effort per the spec.

    def _duplicate_semantic_groups(
        self, unmatched_a: list[str], unmatched_b: list[str]
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        tokens_b_cache = {column_b: _tokenize(column_b) for column_b in unmatched_b}

        for column_a in unmatched_a:
            tokens_a = _tokenize(column_a)
            generic_in_a = tokens_a & _GENERIC_TOKENS
            non_generic_a = tokens_a - _GENERIC_TOKENS
            if not generic_in_a or len(non_generic_a) > 1:
                continue

            for generic_token in generic_in_a:
                candidates = [
                    column_b
                    for column_b in unmatched_b
                    if generic_token in tokens_b_cache[column_b]
                ]
                if len(candidates) >= 2:
                    anomalies.append(
                        Anomaly(
                            layer="semantic",
                            severity=Severity.SUGGESTION,
                            column=column_a,
                            message=(
                                f"'{column_a}' (file A) may have been split into "
                                f"multiple columns in file B: {candidates}."
                            ),
                            evidence={
                                "column_a": column_a,
                                "candidate_group": candidates,
                            },
                        )
                    )
                    break  # one grouped anomaly per column_a is enough
        return anomalies
