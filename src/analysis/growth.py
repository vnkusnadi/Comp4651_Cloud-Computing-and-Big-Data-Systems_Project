"""
Multi-year growth questions, answered by code only.

    python -m src.analysis.growth "Which grew revenue faster from 2021 to 2025, Apple or Microsoft?"

Each answer needs two filings per company (the start year's 10-K and the end
year's 10-K), so it tests the part a single-document AI chat finds hardest:
pulling the same line from several reports and comparing them. Values are
picked with Person C's Engine.select_unit (passed, no division rows), every
number keeps its unit_id, page and filing, and growth is computed in code.

Needs evidence units for every year asked about; build them with
    python data_ingestion_and_processing/pipeline.py --years 2021 2022 2023 2024 2025 --out <dir>
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.serve.engine import Engine

REPO = Path(__file__).resolve().parents[2]
MULTI_YEAR_UNITS = REPO / "data_ingestion_and_processing" / "data" / "evidence_units_5y" / "evidence_units.jsonl"
NAMES = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AVGO": "Broadcom", "CSCO": "Cisco"}

# everyday words -> schema concept (only money amounts make sense for growth)
GROWTH_METRICS = [
    (r"net income|profit|earnings", "NetIncomeLoss"),
    (r"operating income", "OperatingIncomeLoss"),
    (r"total assets|assets", "Assets"),
    (r"revenue|sales", "Revenue_total"),
]
GROWTH_WORDS = re.compile(r"\bgr[eo]w|\bgrowth\b|\bincrease[ds]?\b|\bfaster\b", re.I)


def parse_growth(engine: Engine, question: str) -> Optional[Dict[str, Any]]:
    """Return {metric, tickers, fy_from, fy_to} for a growth question, else None."""
    if not GROWTH_WORDS.search(question):
        return None
    q = question.lower()
    metric = next((m for pat, m in GROWTH_METRICS if re.search(pat, q)), None)
    tickers = [t for t in engine.parser.parse(question).companies]
    years = sorted({int(y) for y in re.findall(r"\b(20\d{2})\b", q)})
    if metric is None or len(tickers) < 2 or len(tickers) == len(engine.parser.sector_tickers) and \
            not any(w in q for w in ("all", "five", "sector", "industry")):
        return None
    fy_from, fy_to = (years[0], years[-1]) if len(years) >= 2 else (2021, 2025)
    return {"metric": metric, "tickers": tickers, "fy_from": fy_from, "fy_to": fy_to}


def _value(engine: Engine, ticker: str, metric: str, fy: int, question: str) -> Optional[Dict[str, Any]]:
    u, _ = engine.select_unit(ticker, metric, fy, engine.parser.parse(question))
    if u is None or u.get("value") is None:
        return None
    return {"value": float(u["value"]), "unit_id": u["unit_id"], "page": u.get("source_page"),
            "row_label": u.get("row_label"), "accession_number": u.get("accession_number"),
            "fiscal_year": fy}


def answer_growth(question: str, engine: Engine) -> Dict[str, Any]:
    t0 = time.perf_counter()
    p = parse_growth(engine, question)
    if p is None:
        return {"status": "not_a_growth_question"}
    rows, missing = {}, []
    for t in p["tickers"]:
        a = _value(engine, t, p["metric"], p["fy_from"], question)
        b = _value(engine, t, p["metric"], p["fy_to"], question)
        if a is None or b is None or a["value"] <= 0:
            missing.append({"ticker": t, "missing": [y for y, v in ((p["fy_from"], a), (p["fy_to"], b)) if v is None]
                            or ["start value not positive"]})
            continue
        n = p["fy_to"] - p["fy_from"]
        g = b["value"] / a["value"] - 1
        rows[t] = {"start": a, "end": b, "growth": g,
                   "cagr": (b["value"] / a["value"]) ** (1 / n) - 1 if n > 0 else None}
    winner = max(rows, key=lambda t: rows[t]["growth"]) if len(rows) >= 2 else None
    order = sorted(rows, key=lambda t: -rows[t]["growth"])
    text = ("; ".join(f"{NAMES.get(t, t)} {p['metric']} grew {rows[t]['growth']:.1%} "
                      f"(FY{p['fy_from']} {rows[t]['start']['value'] / 1e9:,.1f}B to "
                      f"FY{p['fy_to']} {rows[t]['end']['value'] / 1e9:,.1f}B)" for t in order)
            + (f". {NAMES.get(winner, winner)} grew faster." if winner else ". Not enough data to compare."))
    if missing:
        text += " Missing: " + "; ".join(f"{NAMES.get(m['ticker'], m['ticker'])} {', '.join(map(str, m['missing']))}"
                                       for m in missing) + "."
    return {"status": "growth_code_only", "parsed": p, "per_company": rows, "missing": missing,
            "winner": winner, "ranking": order, "answer_text": text,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}


if __name__ == "__main__":
    import json
    e = Engine(units_path=MULTI_YEAR_UNITS)
    r = answer_growth(" ".join(sys.argv[1:]) or "Which grew revenue faster from 2021 to 2025, Apple or Microsoft?", e)
    print(r.get("answer_text") or r["status"])
    print(json.dumps(r, indent=2, default=str)[:3000])
