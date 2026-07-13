from __future__ import annotations

import io

import polars as pl
import yaml
from fastapi.testclient import TestClient

from adv_data_comp.comparator import Comparator
from adv_data_comp.serve.app import app

client = TestClient(app)


def _csv_bytes(rows: dict) -> bytes:
    buf = io.BytesIO()
    pl.DataFrame(rows).write_csv(buf)
    return buf.getvalue()


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    pl.DataFrame(rows).write_csv(path)
    return path


class TestHealth:
    def test_health_returns_ok(self):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestVersion:
    def test_version_returns_a_version_string(self):
        response = client.get("/version")

        assert response.status_code == 200
        body = response.json()
        assert "version" in body
        assert isinstance(body["version"], str)
        assert body["version"] != ""


class TestFormats:
    def test_formats_returns_all_six_output_formats(self):
        response = client.get("/formats")

        assert response.status_code == 200
        body = response.json()
        assert set(body["formats"]) == {
            "html",
            "json",
            "yaml",
            "markdown",
            "csv",
            "dbt",
        }


class TestCompare:
    def test_compare_json_matches_direct_comparator_call(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2, 3], "revenue": [10.0, 20.0, 30.0]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2, 3], "revenue": [10.0, 20.0, 35.0]})

        direct_result = Comparator().compare(file_a, file_b)

        with file_a.open("rb") as fa, file_b.open("rb") as fb:
            response = client.post(
                "/compare",
                files={
                    "file_a": ("a.csv", fa, "text/csv"),
                    "file_b": ("b.csv", fb, "text/csv"),
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert "anomalies" in body
        assert "summary" in body
        assert body["summary"] == direct_result.summary
        assert len(body["anomalies"]) == len(direct_result.anomalies)
        assert {a["layer"] for a in body["anomalies"]} == {a.layer for a in direct_result.anomalies}

    def test_compare_report_yaml_returns_yaml_body(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        with file_a.open("rb") as fa, file_b.open("rb") as fb:
            response = client.post(
                "/compare",
                data={"report": "yaml"},
                files={
                    "file_a": ("a.csv", fa, "text/csv"),
                    "file_b": ("b.csv", fb, "text/csv"),
                },
            )

        assert response.status_code == 200
        assert "yaml" in response.headers["content-type"]
        parsed = yaml.safe_load(response.text)
        assert "anomalies" in parsed
        assert "summary" in parsed

    def test_compare_invalid_report_returns_400(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        with file_a.open("rb") as fa, file_b.open("rb") as fb:
            response = client.post(
                "/compare",
                data={"report": "csv"},
                files={
                    "file_a": ("a.csv", fa, "text/csv"),
                    "file_b": ("b.csv", fb, "text/csv"),
                },
            )

        assert response.status_code == 400

    def test_compare_layers_field_restricts_to_requested_layers(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2], "extra": [1, 2]})

        with file_a.open("rb") as fa, file_b.open("rb") as fb:
            response = client.post(
                "/compare",
                data={"layers": "schema"},
                files={
                    "file_a": ("a.csv", fa, "text/csv"),
                    "file_b": ("b.csv", fb, "text/csv"),
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["layers_run"] == ["schema"]
        assert all(a["layer"] == "schema" for a in body["anomalies"])

    def test_compare_key_field_enables_referential_layer(self, tmp_path):
        file_a = _write_csv(tmp_path, "a.csv", {"id": [1, 2, 3]})
        file_b = _write_csv(tmp_path, "b.csv", {"id": [1, 2]})

        with file_a.open("rb") as fa, file_b.open("rb") as fb:
            response = client.post(
                "/compare",
                data={"layers": "referential", "key": "id"},
                files={
                    "file_a": ("a.csv", fa, "text/csv"),
                    "file_b": ("b.csv", fb, "text/csv"),
                },
            )

        assert response.status_code == 200
        body = response.json()
        referential = [a for a in body["anomalies"] if a["layer"] == "referential"]
        assert len(referential) >= 1
        # Without a key, referential layer only emits a single graceful-skip
        # warning anomaly (see Comparator tests) — with a key wired through,
        # it should actually run and find the missing row.
        assert any("not found" in a["message"].lower() for a in referential)
