"""
Person D: check the saved numbers before testing any answers (slide 20).

    python -m evaluation.data_checks

  * accounting identities (gross profit, operating income, EPS) via Person B's checker
  * product + service revenue = total revenue
  * every passed number has a source page
  * coverage: how many category metrics each company has
  * duplicates: the same number saved more than once
Writes evaluation/results/data_checks.json and prints a table.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from src.align.validate import check_identities, revenue_reconciliation
from src.analysis.packet import build_packet
from src.serve.engine import Engine

HERE = Path(__file__).resolve().parent


def main() -> None:
    e = Engine()
    tickers = ["AAPL", "MSFT", "NVDA", "AVGO", "CSCO"]
    out = {"identities": {}, "segment_sum": {}, "source_page_share": {}, "coverage": {}, "duplicates": {}}
    for t in tickers:
        fy = e.latest_fy(t)
        out["identities"][t] = {r["check"]: (round(r["gap"] * 100, 3) if "gap" in r else r["status"])
                                for r in check_identities(e.units, t, fy)}
        try:
            rec = revenue_reconciliation(e.units, t, fy)
            out["segment_sum"][t] = rec
        except Exception as exc:            # signature differences are reported, not hidden
            out["segment_sum"][t] = f"not run: {exc!r}"[:120]
        passed = [u for u in e.units if u["ticker"] == t and u.get("validation_status") == "passed"]
        out["source_page_share"][t] = round(100 * sum(1 for u in passed if u.get("source_page")) / len(passed), 1)
        dup = Counter((u["metric"], u["value"]) for u in passed)
        out["duplicates"][t] = sum(c - 1 for c in dup.values() if c > 1)
    used = with_page = 0
    for cat in e.categories:
        p = build_packet(e, cat, tickers)
        out["coverage"][cat] = {t: f"{c['available']}/{c['of']}" for t, c in p["coverage"].items()}
        for ent in p["metrics"].values():
            for v in ent["values"].values():
                for uid in v["inputs"].values():
                    used += 1
                    with_page += bool(e.by_id[uid].get("source_page"))
    out["source_page_share_used_in_answers"] = round(100 * with_page / used, 1) if used else None

    print("Accounting identities (gap %):")
    for t, r in out["identities"].items():
        print(f"  {t}: {r}")
    print("\nAll passed numbers with a source page (%):", out["source_page_share"])
    print("Numbers actually used in answers with a source page (%):", out["source_page_share_used_in_answers"])
    print("Duplicate passed numbers (same metric and value saved twice):", out["duplicates"])
    print("\nCoverage (metrics found / metrics in category):")
    for c, r in out["coverage"].items():
        print(f"  {c}: {r}")
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "data_checks.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("\nSaved -> evaluation/results/data_checks.json")


if __name__ == "__main__":
    main()
