import itertools

import polars as pl
import pytest

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.duckdb_engine import DuckDBEngine, DuckDBFrame
from adv_data_comp.engine.polars_engine import PolarsEngine
from adv_data_comp.layers.semantic_layer import SemanticLayer, _similarity_score

ENGINES = [PolarsEngine, DuckDBEngine]


@pytest.fixture(params=ENGINES, ids=["polars", "duckdb"])
def engine(request):
    return request.param()


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


def _force_string_column(frame, column: str):
    """Both CSV readers auto-infer digit-only columns as numeric; force the
    column back to a string type per-engine so a test can exercise the
    "string column that happens to parse as int/float/date" scenario
    regardless of which engine's sniffer already coerced it."""
    if isinstance(frame, DuckDBFrame):
        new_view = f"{frame.view_name}_str"
        frame.con.sql(
            f"CREATE OR REPLACE VIEW {new_view} AS "
            f'SELECT * EXCLUDE ("{column}"), CAST("{column}" AS VARCHAR) AS "{column}" '
            f"FROM {frame.view_name}"
        )
        return DuckDBFrame(con=frame.con, view_name=new_view)
    return frame.with_columns(pl.col(column).cast(pl.Utf8))


class TestSimilarityScore:
    """Pure, engine-agnostic tests of the scoring function itself."""

    def test_all_customer_id_variants_score_above_threshold_against_each_other(self):
        variants = ["CustomerID", "customer_id", "cust_id", "CUST_ID"]
        config = ComparisonConfig()
        for name_a, name_b in itertools.combinations(variants, 2):
            score = _similarity_score(name_a, name_b)
            assert score >= config.fuzzy_threshold, (name_a, name_b, score)

    def test_dissimilar_names_do_not_meet_threshold(self):
        config = ComparisonConfig()
        assert _similarity_score("revenue", "shipping_address") < config.fuzzy_threshold
        assert _similarity_score("email", "phone_number") < config.fuzzy_threshold


class TestFuzzyNameMatching:
    def test_matches_a_single_fuzzy_pair_end_to_end(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"CustomerID": [1, 2], "revenue": [1.0, 2.0]})
        path_b = _write_csv(tmp_path, "b.csv", {"cust_id": [1, 2], "revenue": [1.0, 2.0]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        fuzzy = [a for a in anomalies if a.evidence.get("column_b") == "cust_id"]
        assert len(fuzzy) == 1
        anomaly = fuzzy[0]
        assert anomaly.layer == "semantic"
        assert anomaly.severity == "suggestion"
        assert anomaly.column == "CustomerID"
        assert anomaly.evidence["column_a"] == "CustomerID"
        assert anomaly.evidence["suggested_mapping"] == "cust_id"
        assert anomaly.evidence["similarity_score"] >= 0.80

    def test_exactly_matched_columns_are_never_fuzzy_matched(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1, 2]})
        path_b = _write_csv(tmp_path, "b.csv", {"customer_id": [1, 2]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        assert anomalies == []

    def test_greedy_bipartite_matching_does_not_double_claim_columns(self, tmp_path, engine):
        # "customer_id" should be claimed by the best match ("cust_id"), leaving
        # "custid_legacy" unmatched to any remaining column (there is none here,
        # so it should simply not produce a spurious duplicate claim).
        path_a = _write_csv(tmp_path, "a.csv", {"customer_id": [1], "unrelated_col": [1]})
        path_b = _write_csv(tmp_path, "b.csv", {"cust_id": [1], "custid_legacy": [1]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        claimed_a = [a.evidence["column_a"] for a in anomalies if "column_a" in a.evidence]
        claimed_b = [a.evidence["column_b"] for a in anomalies if "column_b" in a.evidence]
        assert len(claimed_a) == len(set(claimed_a))
        assert len(claimed_b) == len(set(claimed_b))


class TestTypeCoercionCandidates:
    def test_string_column_that_parses_cleanly_as_int_is_flagged(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"legacy_code": ["1", "2", "3"]})
        path_b = _write_csv(tmp_path, "b.csv", {"other_col": [1, 2, 3]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        frame_a = _force_string_column(frame_a, "legacy_code")

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        coercion = [a for a in anomalies if a.column == "legacy_code"]
        assert len(coercion) == 1
        assert coercion[0].evidence["candidate_type"] == "int"
        assert coercion[0].severity == "suggestion"

    def test_string_column_that_parses_cleanly_as_float_is_flagged(self, tmp_path):
        engine = PolarsEngine()
        path_a = _write_csv(tmp_path, "a.csv", {"legacy_amount": ["1.5", "2.25", "3.0"]})
        path_b = _write_csv(tmp_path, "b.csv", {"other_col": [1, 2, 3]})
        frame_a = engine.read(path_a).with_columns(pl.col("legacy_amount").cast(pl.Utf8))
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        coercion = [a for a in anomalies if a.column == "legacy_amount"]
        assert len(coercion) == 1
        assert coercion[0].evidence["candidate_type"] == "float"

    def test_string_column_that_parses_cleanly_as_date_is_flagged(self, tmp_path):
        engine = PolarsEngine()
        path_a = _write_csv(
            tmp_path,
            "a.csv",
            {"legacy_date": ["2024-01-01", "2024-02-15", "2024-03-10"]},
        )
        path_b = _write_csv(tmp_path, "b.csv", {"other_col": [1, 2, 3]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        frame_a = _force_string_column(frame_a, "legacy_date")

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        coercion = [a for a in anomalies if a.column == "legacy_date"]
        assert len(coercion) == 1
        assert coercion[0].evidence["candidate_type"] == "date"

    def test_string_column_with_non_parsable_values_is_not_flagged(self, tmp_path):
        engine = PolarsEngine()
        path_a = _write_csv(tmp_path, "a.csv", {"legacy_notes": ["hello", "world", "abc123"]})
        path_b = _write_csv(tmp_path, "b.csv", {"other_col": [1, 2, 3]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        coercion = [a for a in anomalies if a.column == "legacy_notes"]
        assert coercion == []


class TestUnitDifferenceDetection:
    def test_flags_column_with_currency_suffix_against_bare_name(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"revenue_usd": [1.0, 2.0]})
        path_b = _write_csv(tmp_path, "b.csv", {"revenue": [1.0, 2.0]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        unit_anomalies = [a for a in anomalies if "note" in a.evidence]
        assert len(unit_anomalies) == 1
        assert unit_anomalies[0].column == "revenue_usd"
        assert unit_anomalies[0].evidence["column_a"] == "revenue_usd"
        assert unit_anomalies[0].evidence["column_b"] == "revenue"
        assert unit_anomalies[0].severity == "suggestion"

    def test_does_not_flag_columns_with_no_common_stripped_name(self, tmp_path):
        engine = PolarsEngine()
        path_a = _write_csv(tmp_path, "a.csv", {"weight_kg": [1.0]})
        path_b = _write_csv(tmp_path, "b.csv", {"height_cm": [1.0]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        unit_anomalies = [a for a in anomalies if "note" in a.evidence]
        assert unit_anomalies == []


class TestDuplicateSemanticColumns:
    def test_flags_full_name_as_possible_split_of_first_and_last_name(self, tmp_path, engine):
        path_a = _write_csv(tmp_path, "a.csv", {"full_name": ["Alice Smith"]})
        path_b = _write_csv(tmp_path, "b.csv", {"first_name": ["Alice"], "last_name": ["Smith"]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        dup_anomalies = [a for a in anomalies if "candidate_group" in a.evidence]
        assert len(dup_anomalies) == 1
        assert dup_anomalies[0].column == "full_name"
        assert dup_anomalies[0].evidence["column_a"] == "full_name"
        assert set(dup_anomalies[0].evidence["candidate_group"]) == {
            "first_name",
            "last_name",
        }
        assert dup_anomalies[0].severity == "suggestion"

    def test_does_not_flag_when_only_one_candidate_column_exists(self, tmp_path):
        engine = PolarsEngine()
        path_a = _write_csv(tmp_path, "a.csv", {"full_name": ["Alice Smith"]})
        path_b = _write_csv(tmp_path, "b.csv", {"first_name": ["Alice"]})
        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)

        layer = SemanticLayer()
        anomalies = layer.compare(engine, frame_a, frame_b, ComparisonConfig())

        dup_anomalies = [a for a in anomalies if "candidate_group" in a.evidence]
        assert dup_anomalies == []


class TestLayerMetadata:
    def test_layer_name_is_semantic(self):
        assert SemanticLayer.layer_name == "semantic"
