from adv_data_comp.config import ComparisonConfig, OutputFormat


class TestComparisonConfig:
    def test_defaults_run_all_five_layers(self):
        config = ComparisonConfig()

        assert config.layers == ["format", "schema", "semantic", "statistical", "referential"]
        assert config.key is None
        assert config.fuzzy_threshold == 0.80
        assert config.memory_threshold_mb == 500
        assert config.explain is False
        assert config.output_formats == []
        assert config.output_dir == "./"
        assert config.severity_filter is None

    def test_accepts_full_configuration(self):
        config = ComparisonConfig(
            key="customer_id",
            layers=["format", "schema"],
            fuzzy_threshold=0.85,
            memory_threshold_mb=0,
            explain=True,
            output_formats=[OutputFormat.JSON, OutputFormat.HTML],
            output_dir="./results/",
            severity_filter=["critical", "warning"],
        )

        assert config.key == "customer_id"
        assert config.layers == ["format", "schema"]
        assert config.output_formats == [OutputFormat.JSON, OutputFormat.HTML]
        assert config.severity_filter == ["critical", "warning"]

    def test_output_format_values_match_the_report_flag_choices(self):
        assert {f.value for f in OutputFormat} == {
            "html",
            "json",
            "yaml",
            "markdown",
            "csv",
            "dbt",
        }
