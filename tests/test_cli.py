from __future__ import annotations

import json

import polars as pl
import yaml
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


class TestCompareBasic:
    def test_no_anomalies_exits_zero_and_prints_terminal_output(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2, 3], "revenue": [10.0, 20.0, 30.0]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2, 3], "revenue": [10.0, 20.0, 30.0]})

        result = runner.invoke(app, ["compare", str(file_a), str(file_b)])

        assert result.exit_code == 0
        assert "adv-data-comp" in result.stdout
        assert "Comparing:" in result.stdout


class TestFailOnCritical:
    def test_fail_on_critical_exits_1_when_critical_anomaly_present(self, tmp_path):
        # Duplicate key values in file A trigger a CRITICAL referential anomaly.
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 1, 2], "value": [10, 10, 20]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 1, 2], "value": [10, 10, 20]})

        result = runner.invoke(
            app,
            [
                "compare",
                str(file_a),
                str(file_b),
                "--layers",
                "referential",
                "--key",
                "id",
                "--fail-on-critical",
            ],
        )

        assert result.exit_code == 1

    def test_without_fail_on_critical_always_exits_zero(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 1, 2], "value": [10, 10, 20]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 1, 2], "value": [10, 10, 20]})

        result = runner.invoke(
            app,
            [
                "compare",
                str(file_a),
                str(file_b),
                "--layers",
                "referential",
                "--key",
                "id",
            ],
        )

        assert result.exit_code == 0


class TestReportFiles:
    def test_report_json_writes_valid_json_file(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})
        out_dir = tmp_path / "out"

        result = runner.invoke(
            app,
            [
                "compare",
                str(file_a),
                str(file_b),
                "--report",
                "json",
                "--output-dir",
                str(out_dir),
            ],
        )

        assert result.exit_code == 0
        report_path = out_dir / "report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert "anomalies" in data
        assert "summary" in data


class TestQuiet:
    def test_quiet_suppresses_terminal_output_but_still_writes_reports(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})
        out_dir = tmp_path / "out"

        result = runner.invoke(
            app,
            [
                "compare",
                str(file_a),
                str(file_b),
                "--quiet",
                "--report",
                "json",
                "--output-dir",
                str(out_dir),
            ],
        )

        assert result.exit_code == 0
        assert "adv-data-comp" not in result.stdout
        assert "Comparing:" not in result.stdout
        assert (out_dir / "report.json").exists()


class TestConfigFile:
    def test_config_file_values_applied_when_no_cli_override(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})
        out_dir = tmp_path / "out"
        config_path = tmp_path / "config.yml"
        config_path.write_text(
            yaml.safe_dump({"layers": ["schema"], "report": ["json"], "output_dir": str(out_dir)}),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            ["compare", str(file_a), str(file_b), "--config", str(config_path)],
        )

        assert result.exit_code == 0
        report_path = out_dir / "report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["layers_run"] == ["schema"]

    def test_cli_flag_overrides_conflicting_config_file_value(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})
        out_dir = tmp_path / "out"
        config_path = tmp_path / "config.yml"
        config_path.write_text(
            yaml.safe_dump({"layers": ["schema"], "report": ["json"], "output_dir": str(out_dir)}),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "compare",
                str(file_a),
                str(file_b),
                "--config",
                str(config_path),
                "--layers",
                "referential",
                "--key",
                "id",
            ],
        )

        assert result.exit_code == 0
        report_path = out_dir / "report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["layers_run"] == ["referential"]


class TestKeyFlag:
    def test_key_enables_referential_layer_detection(self, tmp_path):
        # file A has an extra row (id=3) that's missing from file B: >5% ratio -> critical.
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2, 3]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        with_key = runner.invoke(
            app,
            [
                "compare",
                str(file_a),
                str(file_b),
                "--layers",
                "referential",
                "--key",
                "id",
            ],
        )
        without_key = runner.invoke(
            app,
            ["compare", str(file_a), str(file_b), "--layers", "referential"],
        )

        assert with_key.exit_code == 0
        assert without_key.exit_code == 0
        assert "not found in file B" in with_key.stdout
        assert "skipped" in without_key.stdout.lower()


class TestAdditionalCommands:
    def test_schema_command_runs_and_prints_output(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2], "name": ["a", "b"]})

        result = runner.invoke(app, ["schema", str(file_a)])

        assert result.exit_code == 0
        assert result.stdout.strip() != ""
        assert "id" in result.stdout

    def test_formats_command_runs_and_prints_output(self):
        result = runner.invoke(app, ["formats"])

        assert result.exit_code == 0
        assert "json" in result.stdout
        assert "html" in result.stdout

    def test_version_command_runs_and_prints_output(self):
        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert result.stdout.strip() != ""

    def test_profile_command_runs_and_prints_output(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2], "name": ["a", "b"]})

        result = runner.invoke(app, ["profile", str(file_a)])

        assert result.exit_code == 0
        assert result.stdout.strip() != ""
        assert "id" in result.stdout
