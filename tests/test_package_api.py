import adv_data_comp


class TestPackagePublicApi:
    def test_exports_the_documented_python_api_surface(self):
        assert adv_data_comp.Comparator is not None
        assert adv_data_comp.ComparisonConfig is not None
        assert adv_data_comp.OutputFormat is not None
        assert adv_data_comp.ComparisonResult is not None
        assert adv_data_comp.Anomaly is not None
        assert adv_data_comp.Severity is not None

    def test_comparator_is_usable_directly_from_the_top_level_import(self, tmp_path):
        import polars as pl

        from adv_data_comp import Comparator

        path_a = tmp_path / "a.csv"
        path_b = tmp_path / "b.csv"
        pl.DataFrame({"id": [1, 2]}).write_csv(path_a)
        pl.DataFrame({"id": [1, 2]}).write_csv(path_b)

        result = Comparator().compare(path_a, path_b)
        assert result.summary is not None
