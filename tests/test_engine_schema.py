import polars as pl
import pytest

from adv_data_comp.engine.duckdb_engine import DuckDBEngine
from adv_data_comp.engine.polars_engine import PolarsEngine

ENGINES = [PolarsEngine, DuckDBEngine]


@pytest.fixture(params=ENGINES, ids=["polars", "duckdb"])
def engine(request):
    return request.param()


class TestSchema:
    def test_returns_column_names_in_order(self, tmp_path, engine):
        path = tmp_path / "data.csv"
        pl.DataFrame({"customer_id": [1], "revenue": [1.0], "name": ["a"]}).write_csv(path)
        frame = engine.read(path)

        schema = engine.schema(frame)

        assert list(schema.keys()) == ["customer_id", "revenue", "name"]

    def test_classifies_numeric_string_columns(self, tmp_path, engine):
        path = tmp_path / "data.csv"
        pl.DataFrame({"customer_id": [1, 2], "name": ["a", "b"]}).write_csv(path)
        frame = engine.read(path)

        schema = engine.schema(frame)

        assert schema["customer_id"].category == "int"
        assert schema["name"].category == "string"
