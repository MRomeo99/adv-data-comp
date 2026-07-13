from adv_data_comp.dev.generate_fixtures import generate_fixtures


class TestGenerateFixtures:
    def test_generates_every_small_fixture_pair(self, tmp_path):
        generate_fixtures(output_dir=tmp_path, include_large=False)

        expected = [
            "same_schema_same_data.parquet",
            "same_schema_same_data.csv",
            "same_schema_diff_values.parquet",
            "same_schema_diff_values.csv",
            "diff_schema_same_data.parquet",
            "diff_schema_same_data.csv",
            "fuzzy_columns_a.csv",
            "fuzzy_columns_b.csv",
            "format_variants_a.csv",
            "format_variants_b.csv",
            "missing_rows_a.parquet",
            "missing_rows_b.parquet",
            "edge_cases/encoding_latin1.csv",
            "edge_cases/mixed_types.csv",
            "edge_cases/currency_formats.csv",
        ]
        for name in expected:
            assert (tmp_path / name).exists(), f"missing fixture: {name}"

    def test_skips_the_large_file_unless_requested(self, tmp_path):
        generate_fixtures(output_dir=tmp_path, include_large=False)

        assert not (tmp_path / "large_file_a.parquet").exists()

    def test_generates_the_large_file_when_requested(self, tmp_path):
        generate_fixtures(output_dir=tmp_path, include_large=True, large_file_rows=100)

        assert (tmp_path / "large_file_a.parquet").exists()
