from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from adv_data_comp import __version__
from adv_data_comp.comparator import Comparator
from adv_data_comp.config import ComparisonConfig, OutputFormat
from adv_data_comp.formatters._shared import build_envelope
from adv_data_comp.formatters.yaml_formatter import YamlFormatter

router = APIRouter()

# Per CLAUDE.md's REST API spec: "report ... default json — only json/yaml
# available via API". The full `--report` set (html/markdown/csv/dbt) is
# CLI/Python-API only — those formats are either not meaningfully
# single-response bodies (html is fine, but csv/dbt are geared at files on
# disk) or are reserved for the richer local interfaces.
_VALID_API_REPORTS = {"json", "yaml"}


def _save_upload(upload: UploadFile, tmpdir: str) -> Path:
    """Persist an uploaded file to `tmpdir`, preserving its extension.

    The comparison engine's `read()` dispatches on file suffix (.csv,
    .parquet, .xlsx, ...), so the temp file must keep the original
    filename's extension. `Path(...).name` strips any directory
    components from the client-supplied filename to avoid path traversal
    while keeping the extension intact.
    """
    filename = upload.filename or "upload"
    dest = Path(tmpdir) / Path(filename).name
    with dest.open("wb") as out:
        out.write(upload.file.read())
    return dest


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version")
def version() -> dict[str, str]:
    return {"version": __version__}


@router.get("/formats")
def formats() -> dict[str, list[str]]:
    return {"formats": [f.value for f in OutputFormat]}


@router.post("/compare")
async def compare(
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    key: str | None = Form(None),
    layers: str | None = Form(None),
    explain: bool = Form(False),
    report: str = Form("json"),
):
    if report not in _VALID_API_REPORTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid report format '{report}'. Only 'json' or 'yaml' "
                "are available via the REST API."
            ),
        )

    config_kwargs: dict = {"explain": explain}
    if key:
        config_kwargs["key"] = key
    if layers:
        config_kwargs["layers"] = [layer.strip() for layer in layers.split(",") if layer.strip()]
    config = ComparisonConfig(**config_kwargs)

    with tempfile.TemporaryDirectory() as tmpdir:
        path_a = _save_upload(file_a, tmpdir)
        path_b = _save_upload(file_b, tmpdir)
        result = Comparator(config).compare(path_a, path_b)

    if report == "yaml":
        return Response(content=YamlFormatter().format(result), media_type="application/x-yaml")

    # Use the same flat envelope the JsonFormatter/YamlFormatter build, so
    # the JSON and YAML API responses stay structurally identical to each
    # other and to what result.to_json()/to_yaml() produce locally.
    return JSONResponse(content=build_envelope(result))
