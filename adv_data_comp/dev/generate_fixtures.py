"""Generates the fixture files listed in CLAUDE.md's "Fixture file matrix".

Run via `make fixtures` (small fixtures only) or `make fixtures ARGS=--large`
to additionally generate the >500MB engine-selection fixture, which is slow
and disk-heavy and therefore opt-in.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"

# ~30 bytes/row on disk as parquet with this schema; comfortably clears
# 500MB combined (with file B) at the default row count.
_DEFAULT_LARGE_FILE_ROWS = 20_000_000


def _same_schema_same_data(output_dir: Path) -> None:
    df = pl.DataFrame(
        {
            "customer_id": list(range(1, 101)),
            "revenue": [float(i * 10) for i in range(100)],
            "signup_date": ["2024-01-01"] * 100,
        }
    )
    df.write_parquet(output_dir / "same_schema_same_data.parquet")
    df.write_csv(output_dir / "same_schema_same_data.csv")


def _same_schema_diff_values(output_dir: Path) -> None:
    df_a = pl.DataFrame({"customer_id": list(range(1, 101)), "revenue": [100.0] * 100})
    # File B: same schema, but 15% nulls and a mean shift - statistical layer bait.
    revenue_b = [None if i % 7 == 0 else 140.0 for i in range(100)]
    df_b = pl.DataFrame({"customer_id": list(range(1, 101)), "revenue": revenue_b})
    df_a.write_parquet(output_dir / "same_schema_diff_values.parquet")
    df_b.write_csv(output_dir / "same_schema_diff_values.csv")


def _diff_schema_same_data(output_dir: Path) -> None:
    df_a = pl.DataFrame({"customer_id": list(range(1, 11)), "revenue": [1.0] * 10})
    # File B: customer_id becomes a string column - schema layer bait.
    df_b = pl.DataFrame(
        {"customer_id": [str(i) for i in range(1, 11)], "revenue": [1.0] * 10}
    )
    df_a.write_parquet(output_dir / "diff_schema_same_data.parquet")
    df_b.write_csv(output_dir / "diff_schema_same_data.csv")


def _fuzzy_columns(output_dir: Path) -> None:
    df_a = pl.DataFrame({"CustomerID": list(range(1, 11)), "full_name": ["a"] * 10})
    df_b = pl.DataFrame({"cust_id": list(range(1, 11)), "first_name": ["a"] * 10})
    df_a.write_csv(output_dir / "fuzzy_columns_a.csv")
    df_b.write_csv(output_dir / "fuzzy_columns_b.csv")


def _format_variants(output_dir: Path) -> None:
    (output_dir / "format_variants_a.csv").write_text(
        "customer_id,amount,signup_date\n1,$1200.50,2024-01-15\n2,$980.00,2024-02-20\n",
        encoding="utf-8",
    )
    (output_dir / "format_variants_b.csv").write_text(
        "customer_id,amount,signup_date\n1,1200,50,01/15/2024\n2,980,00,02/20/2024\n",
        encoding="utf-8",
    )


def _missing_rows(output_dir: Path) -> None:
    df_a = pl.DataFrame({"customer_id": list(range(1, 21)), "name": ["a"] * 20})
    df_b = pl.DataFrame({"customer_id": list(range(1, 16)), "name": ["a"] * 15})
    df_a.write_parquet(output_dir / "missing_rows_a.parquet")
    df_b.write_parquet(output_dir / "missing_rows_b.parquet")


def _edge_cases(output_dir: Path) -> None:
    edge_dir = output_dir / "edge_cases"
    edge_dir.mkdir(parents=True, exist_ok=True)

    (edge_dir / "encoding_latin1.csv").write_bytes(
        "name,city\nJos\xe9,S\xe3o Paulo\n".encode("latin-1")
    )

    (edge_dir / "mixed_types.csv").write_text(
        "id,value\n1,100\n2,not_a_number\n3,300\n", encoding="utf-8"
    )

    (edge_dir / "currency_formats.csv").write_text(
        "customer_id,price\n1,$19.99\n2,€25.00\n3,\xa330.00\n", encoding="utf-8"
    )


def _large_file(output_dir: Path, rows: int) -> None:
    df = pl.DataFrame(
        {
            "customer_id": range(rows),
            "revenue": [float(i % 1000) for i in range(rows)],
            "region": ["us", "eu", "apac"] * (rows // 3) + ["us"] * (rows % 3),
        }
    )
    df.write_parquet(output_dir / "large_file_a.parquet")


def generate_fixtures(
    output_dir: Path = FIXTURES_DIR,
    include_large: bool = False,
    large_file_rows: int = _DEFAULT_LARGE_FILE_ROWS,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    _same_schema_same_data(output_dir)
    _same_schema_diff_values(output_dir)
    _diff_schema_same_data(output_dir)
    _fuzzy_columns(output_dir)
    _format_variants(output_dir)
    _missing_rows(output_dir)
    _edge_cases(output_dir)

    if include_large:
        _large_file(output_dir, large_file_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--large",
        action="store_true",
        help="Also generate the >500MB engine-selection fixture (slow, disk-heavy).",
    )
    args = parser.parse_args()
    generate_fixtures(include_large=args.large)
    print(f"Fixtures written to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
