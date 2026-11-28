"""
Many-company question, answered by code only:

    python -m src.analysis.sector_rank                       # revenue growth, FY2020 -> FY2025, all companies
    python -m src.analysis.sector_rank --metric NetIncomeLoss --from 2021 --to 2025

"Rank all 30 IT companies by revenue growth from 2020 to 2025" needs one
number from each of 60 annual reports. A chat AI would have to read all of
those reports at once, which is far more text than its context window holds.
Here the code picks each number the same way the 33-question test does
(Engine.select_unit: passed, whole-company rows only), keeps its unit_id,
page and filing, and computes growth in code.

Needs the 30-company data:
    python data_ingestion_and_processing/pipeline.py --tickers IT30 \
        --years 2020 2021 2022 2023 2024 2025 --out data/evidence_units_it30
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.serve.engine import Engine

REPO = Path(__file__).resolve().parents[2]
IT30_UNITS = REPO / "data_ingestion_and_processing" / "data" / "evidence_units_it30" / "evidence_units.jsonl"


def _pick(engine: Engine, ticker: str, metric: str, fy: int) -> Optional[Dict[str, Any]]:
    u, _ = engine.select_unit(ticker, metric, fy, engine.parser.parse(metric.replace("_", " ")))
    if u is None or u.get("value") is None:
        return None
    return {"value": float(u["value"]), "unit_id": u["unit_id"], "page": u.get("source_page"),
            "row_label": u.get("row_label"), "accession_number": u.get("accession_number"),
            "fiscal_year": fy}


def rank_growth(engine: Engine, metric: str = "Revenue_total", fy_from: int = 2020, fy_to: int = 2025,
                tickers: Optional[List[str]] = None) -> Dict[str, Any]:
    t0 = time.perf_counter()
    tickers = tickers or sorted({u["ticker"] for u in engine.units})
    rows, missing = [], []
    for t in tickers:
        a, b = _pick(engine, t, metric, fy_from), _pick(engine, t, metric, fy_to)
        if a is None or b is None or a["value"] <= 0:
            missing.append({"ticker": t, "missing": [y for y, v in ((fy_from, a), (fy_to, b)) if v is None]
                            or ["start value not positive"]})
            continue
        n = fy_to - fy_from
        rows.append({"ticker": t, "start": a, "end": b, "growth": b["value"] / a["value"] - 1,
                     "cagr": (b["value"] / a["value"]) ** (1 / n) - 1 if n > 0 else None})
    rows.sort(key=lambda r: -r["growth"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"metric": metric, "fy_from": fy_from, "fy_to": fy_to, "companies_asked": len(tickers),
            "ranked": rows, "missing": missing, "reports_used": 2 * len(rows),
            "answer_ms": round((time.perf_counter() - t0) * 1000, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="Revenue_total")
    ap.add_argument("--from", dest="fy_from", type=int, default=2020)
    ap.add_argument("--to", dest="fy_to", type=int, default=2025)
    ap.add_argument("--units", default=str(IT30_UNITS))
    ap.add_argument("--save", default=None, help="write the result JSON here")
    a = ap.parse_args()
    t0 = time.perf_counter()
    e = Engine(units_path=a.units)
    build_s = time.perf_counter() - t0
    r = rank_growth(e, a.metric, a.fy_from, a.fy_to)
    r["engine_build_s"] = round(build_s, 2)
    r["units_loaded"] = len(e.units)
    for row in r["ranked"]:
        print(f"{row['rank']:>2}. {row['ticker']:<5} {row['growth']:>8.1%}  (CAGR {row['cagr']:.1%})  "
              f"FY{a.fy_from} {row['start']['value'] / 1e9:,.1f}B p{row['start']['page']} -> "
              f"FY{a.fy_to} {row['end']['value'] / 1e9:,.1f}B p{row['end']['page']}")
    if r["missing"]:
        print("Missing:", ", ".join(f"{m['ticker']} {m['missing']}" for m in r["missing"]))
    print(f"{r['reports_used']} reports used, answered in {r['answer_ms']} ms "
          f"(loading {r['units_loaded']} saved numbers took {r['engine_build_s']} s)")
    if a.save:
        Path(a.save).write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
        print("Saved ->", a.save)


if __name__ == "__main__":
    main()
