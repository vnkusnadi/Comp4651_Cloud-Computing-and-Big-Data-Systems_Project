"""
Person D switch-off test: what happens WITHOUT the division check?

    python -m evaluation.ablation_division_check

Normally only whole-company rows (no division/product/region dimension) may be
used for totals like Revenue_total. Here we switch that filter off and let the
largest matching number win, which is what a naive search does. We count:
  * how many (company, metric) picks change to a division row
  * how much the numbers move
  * whether any category verdict flips
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Dict, List

from src.analysis.packet import build_packet
from src.analysis.synthesize import code_only_verdict
from src.serve.engine import Engine

HERE = Path(__file__).resolve().parent
TICKERS = ["AAPL", "MSFT", "NVDA", "AVGO", "CSCO"]


class NoDivisionCheckEngine(Engine):
    """Same engine, but any row for the metric may be picked, including
    division rows and rows that were not validated. Picks the largest value,
    like a search that grabs the most prominent 'revenue' number."""

    def select_unit(self, ticker, canonical, fy, parsed):
        metric = self.metric_of(canonical)
        cands = [u for u in self.units
                 if u.get("ticker") == ticker and u.get("metric") == metric
                 and u.get("fiscal_year") == fy and u.get("value") is not None
                 and u.get("validation_status") in ("passed", "rejected", "pending")
                 and (u.get("dimensions") or u.get("validation_status") == "passed")]
        if not cands:
            return None, {}
        # among division rows prefer the biggest; this is the classic mistake
        dim = [u for u in cands if u.get("dimensions")]
        pick = max(dim or cands, key=lambda u: abs(float(u["value"])))
        return pick, {"ablation": "division_check_off"}


def main() -> None:
    normal, broken = Engine(), NoDivisionCheckEngine()
    changed: List[Dict[str, Any]] = []
    flips: List[Dict[str, Any]] = []
    for cat in normal.categories:
        for pair in itertools.combinations(TICKERS, 2):
            a = build_packet(normal, cat, list(pair))
            b = build_packet(broken, cat, list(pair))
            for m in a["metrics"]:
                for t in pair:
                    va = a["metrics"][m]["values"].get(t)
                    vb = b["metrics"][m]["values"].get(t)
                    if va and vb and abs(va["value"] - vb["value"]) > 1e-9 * max(1, abs(va["value"])):
                        changed.append({"category": cat, "pair": pair, "ticker": t, "metric": m,
                                        "correct": va["value"], "without_check": vb["value"],
                                        "change_%": round(100 * (vb["value"] - va["value"]) / abs(va["value"]), 1)})
            wa, wb = code_only_verdict(a)["winner"], code_only_verdict(b)["winner"]
            if wa != wb:
                flips.append({"category": cat, "pair": pair, "correct_winner": wa, "without_check": wb})
    uniq = {(c["ticker"], c["metric"]) for c in changed}
    print(f"Numbers that change when the division check is off: {len(uniq)} (company, metric) pairs")
    for t, m in sorted(uniq)[:20]:
        c = next(x for x in changed if x["ticker"] == t and x["metric"] == m)
        print(f"  {t:5s} {m:32s} {c['correct']:>20,.4f} -> {c['without_check']:>20,.4f}  ({c['change_%']:+}%)")
    print(f"\nCategory verdicts that flip (code-only ranking, 30 pair questions): {len(flips)}")
    for f in flips:
        print(f"  {f['category']:22s} {'/'.join(f['pair'])}: {f['correct_winner']} -> {f['without_check']}")
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "ablation_division_check.json").write_text(
        json.dumps({"changed": changed, "flips": flips}, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
