from __future__ import annotations

import polars as pl
import pytest

from adv_data_comp.config import ComparisonConfig
from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine
from adv_data_comp.layers.format_layer import FormatLayer

ENGINES = [PolarsEngine, DuckDBEngine]


@pytest.fixture(params=ENGINES, ids=["polars", "duckdb"])
def engine(request):
    return request.param()


@pytest.fixture
def config():
    return ComparisonConfig()


def _by_evidence_key(anomalies, key):
    return [a for a in anomalies if key in a.evidence]


def _by_column(anomalies, column):
    return [a for a in anomalies if a.column == column]


class TestFileEncoding:
    def test_flags_encoding_difference_between_csv_files(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        # a readable stand-in used only to obtain a valid frame_b for the interface --
        # the raw invalid-utf8 file below can't be read by either engine at all.
        path_b_readable = tmp_path / "b_readable.csv"
        path_b = tmp_path / "b.csv"

        path_a.write_text("id,name\n1,alice\n2,bob\n", encoding="utf-8")
        path_b_readable.write_text("id,name\n1,alice\n2,bob\n", encoding="utf-8")
        # 0xe9 is a lone byte invalid as a UTF-8 continuation byte, but a valid latin-1 char
        path_b.write_bytes(b"id,name\n1,alice\n2,caf\xe9\n")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b_readable)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        encoding_anomalies = _by_evidence_key(anomalies, "encoding_a")
        assert len(encoding_anomalies) == 1
        anomaly = encoding_anomalies[0]
        assert anomaly.layer == "format"
        assert anomaly.column == "__file__"
        assert anomaly.severity == "info"
        assert anomaly.evidence["encoding_a"] == "utf-8"
        assert anomaly.evidence["encoding_b"] == "latin-1"

    def test_no_encoding_anomaly_when_both_files_are_utf8(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_text("id,name\n1,alice\n", encoding="utf-8")
        path_b.write_text("id,name\n1,alice\n", encoding="utf-8")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert _by_evidence_key(anomalies, "encoding_a") == []

    def test_skips_encoding_check_for_parquet_files(self, tmp_path, engine, config):
        path_a = tmp_path / "a.parquet"
        path_b = tmp_path / "b.parquet"
        pl.DataFrame({"id": [1, 2], "name": ["alice", "bob"]}).write_parquet(path_a)
        pl.DataFrame({"id": [1, 2], "name": ["carol", "dave"]}).write_parquet(path_b)

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert _by_column(anomalies, "__file__") == []


class TestLineEndings:
    def test_flags_line_ending_difference(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_bytes(b"id,name\r\n1,alice\r\n2,bob\r\n")
        path_b.write_bytes(b"id,name\n1,alice\n2,bob\n")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        le_anomalies = _by_evidence_key(anomalies, "line_ending_a")
        assert len(le_anomalies) == 1
        anomaly = le_anomalies[0]
        assert anomaly.column == "__file__"
        assert anomaly.severity == "info"
        assert anomaly.evidence["line_ending_a"] == "CRLF"
        assert anomaly.evidence["line_ending_b"] == "LF"

    def test_no_anomaly_when_line_endings_match(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_bytes(b"id,name\n1,alice\n")
        path_b.write_bytes(b"id,name\n1,alice\n")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert _by_evidence_key(anomalies, "line_ending_a") == []


class TestDecimalSeparator:
    def test_flags_dot_vs_comma_decimal_separator(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_text("id,amount\n1,123.45\n2,45.6\n3,999.99\n", encoding="utf-8")
        path_b.write_text('id,amount\n1,"123,45"\n2,"45,6"\n3,"999,99"\n', encoding="utf-8")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        amount_anomalies = [
            a for a in _by_column(anomalies, "amount") if "decimal separator" in a.message.lower()
        ]
        assert len(amount_anomalies) == 1
        anomaly = amount_anomalies[0]
        assert anomaly.severity == "info"
        assert "example_a" in anomaly.evidence
        assert "example_b" in anomaly.evidence


class TestCurrencySymbols:
    def test_flags_currency_symbol_present_in_only_one_file(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_text('id,price\n1,"$100"\n2,"$250.50"\n3,"$5"\n', encoding="utf-8")
        path_b.write_text("id,price\n1,100\n2,250.50\n3,5\n", encoding="utf-8")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        price_anomalies = [
            a for a in _by_column(anomalies, "price") if "currency" in a.message.lower()
        ]
        assert len(price_anomalies) == 1
        anomaly = price_anomalies[0]
        assert anomaly.severity == "info"
        assert anomaly.evidence["symbol"] == "$"
        assert "example_a" in anomaly.evidence
        assert "example_b" in anomaly.evidence


class TestBooleanRepresentations:
    def test_flags_different_boolean_token_conventions(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_text("id,active\n1,yes\n2,no\n3,yes\n", encoding="utf-8")
        path_b.write_text("id,active\n1,y\n2,n\n3,y\n", encoding="utf-8")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        active_anomalies = [
            a for a in _by_column(anomalies, "active") if "boolean" in a.message.lower()
        ]
        assert len(active_anomalies) == 1
        assert active_anomalies[0].severity == "info"

    def test_no_anomaly_when_boolean_tokens_match(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_text("id,active\n1,yes\n2,no\n", encoding="utf-8")
        path_b.write_text("id,active\n1,yes\n2,no\n", encoding="utf-8")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        active_anomalies = [
            a for a in _by_column(anomalies, "active") if "boolean" in a.message.lower()
        ]
        assert active_anomalies == []


class TestDateFormat:
    def test_flags_different_date_format_conventions(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        # "Jan 15 2024" style is used for file B because DuckDB's CSV sniffer
        # auto-detects plain ISO/slash dates as a native DATE column on both
        # sides (collapsing the very format difference this check looks for);
        # a month-name format reliably stays VARCHAR/string in both engines.
        path_a.write_text(
            "id,signup_date\n1,2024-01-15\n2,2024-02-20\n3,2024-03-01\n",
            encoding="utf-8",
        )
        path_b.write_text(
            "id,signup_date\n1,Jan 15 2024\n2,Feb 20 2024\n3,Mar 01 2024\n",
            encoding="utf-8",
        )

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        date_anomalies = [
            a for a in _by_column(anomalies, "signup_date") if "date format" in a.message.lower()
        ]
        assert len(date_anomalies) == 1
        anomaly = date_anomalies[0]
        assert anomaly.severity == "info"
        assert anomaly.evidence["format_a"] == "%Y-%m-%d"
        assert anomaly.evidence["format_b"] == "%b %d %Y"


class TestQuotedVsUnquotedStrings:
    def test_flags_quoting_convention_difference(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        path_a.write_text('id,name\n1,"alice"\n2,"bob"\n3,"carol"\n4,"dave"\n', encoding="utf-8")
        path_b.write_text("id,name\n1,alice\n2,bob\n3,carol\n4,dave\n", encoding="utf-8")

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        quoting_anomalies = _by_evidence_key(anomalies, "quoting_a")
        assert len(quoting_anomalies) == 1
        anomaly = quoting_anomalies[0]
        assert anomaly.column == "__file__"
        assert anomaly.severity == "info"
        assert anomaly.evidence["quoting_a"] == "quoted"
        assert anomaly.evidence["quoting_b"] == "unquoted"


class TestNoAnomaliesBaseline:
    def test_identical_files_produce_no_anomalies(self, tmp_path, engine, config):
        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        pl.DataFrame({"id": [1, 2, 3], "name": ["alice", "bob", "carol"]}).write_csv(path_a)
        pl.DataFrame({"id": [1, 2, 3], "name": ["alice", "bob", "carol"]}).write_csv(path_b)

        frame_a = engine.read(path_a)
        frame_b = engine.read(path_b)
        layer = FormatLayer(path_a, path_b)

        anomalies = layer.compare(engine, frame_a, frame_b, config)

        assert anomalies == []


def test_layer_name_is_format():
    assert FormatLayer.layer_name == "format"
