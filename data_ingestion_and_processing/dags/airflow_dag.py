"""
Airflow DAG for Person A's ingestion stage.

Runs one build task per ticker in parallel, then merges the partial
results into the canonical `evidence_units.parquet` / `.jsonl`.

The merge produces the SAME files as `python pipeline.py` — the only
difference is that each ticker is fetched/parsed concurrently and can
retry independently.
"""

import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator

# Imported from /opt/airflow/pipeline.py, mounted via docker-compose.yml.
from pipeline import (
    TICKERS,
    TARGET_FISCAL_YEAR,
    build_evidence_units_for_company,
)

# Absolute path inside the container; maps to ./data on the host.
OUTPUT_DIR = "/opt/airflow/data/evidence_units"
PARTIALS_DIR = f"{OUTPUT_DIR}/partials"


# ---------------------------------------------------------------------------
# Task callables
# ---------------------------------------------------------------------------

def _build_for_ticker(ticker: str, fiscal_year: int, **context):
    """
    Stage 3 for one ticker. Writes units to a partial JSON file so the
    consolidate task can merge without re-fetching. Pushes the unit count
    to XCom for the UI.
    """
    print(f"[{ticker}] Stage 3 start (FY{fiscal_year})")
    units = build_evidence_units_for_company(ticker, fiscal_year=fiscal_year)

    partial_path = Path(PARTIALS_DIR) / f"{ticker}.json"
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path.write_text(
        json.dumps([u.to_dict() for u in units], default=str),
        encoding="utf-8",
    )

    print(f"[{ticker}] wrote {len(units)} units → {partial_path}")
    context["ti"].xcom_push(key=f"{ticker}_count", value=len(units))
    return len(units)


def _consolidate(tickers: list, fiscal_year: int, **context):
    """
    Merge all per-ticker partials into the canonical Parquet + JSONL.
    Fails loudly if any ticker's partial is missing (which would mean a
    build task silently didn't run).
    """
    partials = Path(PARTIALS_DIR)
    missing = [t for t in tickers if not (partials / f"{t}.json").exists()]
    if missing:
        raise RuntimeError(f"Missing partials for: {missing}")

    all_units = []
    for ticker in tickers:
        units = json.loads((partials / f"{ticker}.json").read_text(encoding="utf-8"))
        all_units.extend(units)
        print(f"[{ticker}] loaded {len(units)} units")

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "evidence_units.jsonl").open("w", encoding="utf-8") as fh:
        for u in all_units:
            fh.write(json.dumps(u, default=str) + "\n")

    df = pd.DataFrame(all_units)
    if "dimensions" in df.columns:
        df["dimensions"] = df["dimensions"].apply(
            lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x
        )
    df.to_parquet(out_dir / "evidence_units.parquet", index=False)

    rejected = (df["validation_status"] == "rejected").sum() if len(df) else 0
    print(f"\nConsolidated {len(all_units)} units "
          f"({rejected} rejected) → {out_dir}")

        # Verify the canonical evidence exists for every ticker in the cohort
    DEMO_TICKERS = ["AAPL", "MSFT", "NVDA", "AVGO", "CSCO"]
    for ticker in DEMO_TICKERS:
        rev = df[(df.ticker == ticker) & (df.metric == "Revenue_total")
                 & (df.mapping_method == "xbrl_tag")
                 & (df.validation_status == "passed")]
        rnd = df[(df.ticker == ticker) & (df.metric == "ResearchAndDevelopmentExpense")
                 & (df.mapping_method == "xbrl_tag")
                 & (df.validation_status == "passed")]
        if len(rev) != 1:
            raise RuntimeError(
                f"{ticker}: expected 1 passed xbrl_tag Revenue_total, "
                f"got {len(rev)}"
            )
        if len(rnd) != 1:
            raise RuntimeError(
                f"{ticker}: expected 1 passed xbrl_tag R&D_expense, "
                f"got {len(rnd)}"
            )
        # Per-ticker rejection count: each company must have rejected at
        # least one segment-revenue candidate, or the adversarial test
        # case isn't exercised for that company.
        n_rej = len(df[(df.ticker == ticker) & (df.validation_status == "rejected")
                       & (df.metric.isin(["Revenue_total", "Revenue_product",
                                          "Revenue_service"]))])
        if n_rej == 0:
            raise RuntimeError(
                f"{ticker}: zero rejected revenue candidates — adversarial "
                f"test case not exercised for this company"
            )

    print(f"\nCohort invariants passed for {DEMO_TICKERS}")
    if rejected == 0:
        raise RuntimeError(
            "Zero rejected units — adversarial test case not exercised "
            "(segment revenue rejections missing)."
        )

    context["ti"].xcom_push(key="total_units", value=len(all_units))
    context["ti"].xcom_push(key="rejected_units", value=int(rejected))
    return len(all_units)


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

default_args = {
    "owner": "person-a",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    "it_sector_ingest_and_build_units",
    default_args=default_args,
    description="Ingest IT-sector 10-K filings and build evidence units (Person A)",
    schedule_interval=None,        # manual trigger for the vertical slice
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["person-a", "ingestion", "it-sector"],
) as dag:

    build_tasks = []
    for ticker in TICKERS:
        build_tasks.append(
            PythonOperator(
                task_id=f"build_units_{ticker.lower()}",
                python_callable=_build_for_ticker,
                op_kwargs={
                    "ticker": ticker,
                    "fiscal_year": TARGET_FISCAL_YEAR,
                },
            )
        )

    consolidate = PythonOperator(
        task_id="consolidate_and_write",
        python_callable=_consolidate,
        op_kwargs={
            "tickers": TICKERS,
            "fiscal_year": TARGET_FISCAL_YEAR,
        },
    )

    build_tasks >> consolidate