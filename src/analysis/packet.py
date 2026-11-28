"""
Stage 5b: Category Analysis Packet ("fact sheet").

Code builds this BEFORE any AI speaks. For one category question it holds:
  * every member metric value per company, with the formula and the unit_ids
    of the inputs (so every number traces back to a 10-K table cell)
  * per-metric leader, peer median, delta vs median, percentile rank
  * coverage: which metrics are missing for which company
  * Pareto check: does one company beat or tie every other on every metric?
  * stability grid: does the winner change if we weigh the metrics differently?

Base values are picked with Person C's Engine.select_unit (same filters:
validation_status=passed, no division rows for consolidated concepts), so the
packet and the per-metric answers always agree on which row was used.
Derived metrics are computed here from the schema formula, including
formulas with + and - (EBITDA, free cash flow), never by the LLM.
"""

from __future__ import annotations

import random
import statistics
from typing import Any, Dict, List, Optional, Tuple

from src.serve.engine import Engine
from src.serve.query_parser import ParsedQuery

STABILITY_SAMPLES = 1000
STABILITY_SEED = 4651
STABLE_SHARE = 0.95          # winner wins >= 95% of random weightings -> "stable"
MIXED_SHARE = 0.60           # 60-95% -> "mixed", below -> "fragile"
EMPHASIS_WEIGHT = 3.0        # "revenue-weighted" etc. = that metric counts 3x
# Base values that only count when they come from a row of a financial
# statement table, not from a sentence in the notes.
STATEMENT_ROW_ONLY = {"DepreciationAndAmortization"}


from src.util.formula import _formula_names, _safe_formula, eval_formula  # noqa: E402


# --------------------------------------------------------------------------- packet
def _direction(category: Dict[str, Any]) -> str:
    return category.get("direction", "higher_is_better")


def _metric_value(engine: Engine, ticker: str, metric: str, fy: int,
                  parsed: ParsedQuery, depth: int = 0) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Return ({value, unit, unit_ids, inputs, formula, ...}, missing_inputs)."""
    if depth > 3:
        return None, [metric]
    if metric in engine.base:
        u, _ = engine.select_unit(ticker, metric, fy, parsed)
        if u is None or u.get("value") is None:
            return None, [metric]
        if metric in STATEMENT_ROW_ONLY and not u.get("row_label"):
            # e.g. Cisco tags "depreciation of property and equipment was $700M" in a
            # policy note; that leaves out amortization, so EBITDA would be too low.
            return None, [f"{metric}_not_from_a_statement_row"]
        return {
            "value": float(u["value"]), "unit": u.get("unit") or "",
            "unit_ids": [u["unit_id"]], "formula": None, "formula_id": None,
            "inputs": {metric: u["unit_id"]},
            "source": {"page": u.get("source_page"), "table_id": u.get("table_id"),
                       "row_label": u.get("row_label"), "section": u.get("section_path")},
            "row_label": u.get("row_label") or "",
        }, []
    d = engine.derived.get(metric)
    if d is None:
        return None, [metric]
    names = _formula_names(_safe_formula(d["formula"]))
    vals, ids, inputs, missing = {}, [], {}, []
    for n in names:
        real = n.replace("_and_", "&")
        sub, miss = _metric_value(engine, ticker, real, fy, parsed, depth + 1)
        if sub is None:
            missing.extend(miss)
            continue
        vals[real] = sub["value"]
        ids.extend(sub["unit_ids"])
        inputs.update(sub["inputs"])
    if missing:
        return None, sorted(set(missing))
    try:
        value = eval_formula(d["formula"], vals)
    except ZeroDivisionError:
        return None, ["denominator_zero"]
    is_ratio = "/" in d["formula"]
    if is_ratio:
        den = d.get("denominator")
        if den in vals and vals[den] <= 0:
            return None, ["denominator_nonpositive"]
    return {
        "value": value, "unit": "ratio" if is_ratio else "USD",
        "unit_ids": list(dict.fromkeys(ids)), "formula": d["formula"],
        "formula_id": d.get("formula_id"), "inputs": inputs, "source": None, "row_label": "",
    }, []


def _rank_score(values: Dict[str, float]) -> Dict[str, float]:
    """Min-max normalise so every metric counts on the same 0..1 scale."""
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {t: 1.0 for t in values}
    return {t: (v - lo) / (hi - lo) for t, v in values.items()}


def _winner(scores: Dict[str, Dict[str, float]], weights: Dict[str, float], tickers: List[str]) -> Tuple[str, Dict[str, float]]:
    total = {t: 0.0 for t in tickers}
    wsum = sum(weights.values()) or 1.0
    for m, w in weights.items():
        for t in tickers:
            total[t] += w * scores[m][t] / wsum
    best = max(total.items(), key=lambda kv: (round(kv[1], 12), kv[0]))
    return best[0], total


def build_packet(engine: Engine, category_name: str, tickers: List[str],
                 fiscal_year: Optional[int] = None, query: str = "") -> Dict[str, Any]:
    category = engine.categories[category_name]
    direction = _direction(category)
    members: List[str] = list(category["members"])
    parsed = engine.parser.parse(query or f"{category_name} {' '.join(tickers)}")

    fy_used = {t: fiscal_year or engine.latest_fy(t) for t in tickers}
    filing_dates = {}
    for t in tickers:
        ds = [u.get("filing_date") for u in engine.units
              if u.get("ticker") == t and u.get("fiscal_year") == fy_used[t] and u.get("filing_date")]
        filing_dates[t] = max(ds) if ds else None

    metrics: Dict[str, Dict[str, Any]] = {}
    missing: List[Dict[str, Any]] = []
    estimates: List[Dict[str, Any]] = []
    for m in members:
        per: Dict[str, Any] = {}
        for t in tickers:
            v, miss = _metric_value(engine, t, m, fy_used[t], parsed) if fy_used[t] else (None, ["no_filing"])
            if v is None:
                missing.append({"ticker": t, "metric": m, "missing_inputs": miss})
                continue
            per[t] = v
            # "Same name, different meaning": combined SG&A reported as selling & marketing
            for inp, uid in v["inputs"].items():
                u = engine.by_id.get(uid, {})
                if inp == "SellingAndMarketingExpense" and "administrative" in (u.get("row_label") or "").lower():
                    estimates.append({"ticker": t, "metric": m, "reason":
                                      "selling & marketing reported together with admin (SG&A); treat as an estimate"})
        entry: Dict[str, Any] = {"values": per, "available_for": sorted(per)}
        if len(per) >= 2:
            vals = {t: per[t]["value"] for t in per}
            med = statistics.median(vals.values())
            order = sorted(vals, key=lambda t: -vals[t])
            entry.update({
                "median": med,
                "highest": order[0], "lowest": order[-1],
                "ranking_high_to_low": order,
                "delta_vs_median": {t: vals[t] - med for t in vals},
                "percentile_rank": {t: (len(order) - 1 - order.index(t)) / (len(order) - 1) for t in vals},
                "leader": order[0] if direction != "profile_comparison" else None,
                "gap_leader_vs_second": (vals[order[0]] - vals[order[1]]) / abs(vals[order[1]])
                                        if vals[order[1]] else None,
            })
        metrics[m] = entry

    coverage = {}
    for t in tickers:
        have = sum(1 for m in members if t in metrics[m]["values"])
        coverage[t] = {"available": have, "of": len(members), "share": have / len(members),
                       "meets_min": have / len(members) >= category.get("min_coverage", 0.7)}

    # Only metrics every company has can be compared fairly. If the schema
    # lists score_members, only those decide the verdict; the rest are shown.
    scored = category.get("score_members") or members
    common = [m for m in scored if set(tickers) <= set(metrics[m]["values"])]
    packet: Dict[str, Any] = {
        "packet_version": "0.1",
        "category": category_name, "direction": direction, "companies": tickers,
        "fiscal_year_used": fy_used, "filing_dates": filing_dates,
        "metrics": metrics, "common_metrics": common, "scored_members": list(scored),
        "missing": missing,
        "estimates": estimates, "coverage": coverage,
    }

    if direction == "profile_comparison" or len(tickers) < 2 or not common:
        packet.update({"pareto": None, "stability": None,
                       "code_verdict": {"type": "profile" if direction == "profile_comparison" else "insufficient",
                                        "winner": None}})
        if direction == "profile_comparison":
            packet["profile"] = {t: {"highest_on": [m for m in common if metrics[m]["highest"] == t],
                                     "lowest_on": [m for m in common if metrics[m]["lowest"] == t]}
                                 for t in tickers}
        return packet

    vals = {m: {t: metrics[m]["values"][t]["value"] for t in tickers} for m in common}

    # ---- Pareto: A dominates B if A >= B on every common metric and > on one
    def dominates(a: str, b: str) -> bool:
        return all(vals[m][a] >= vals[m][b] for m in common) and any(vals[m][a] > vals[m][b] for m in common)

    dominated_by = {t: [o for o in tickers if o != t and dominates(o, t)] for t in tickers}
    dominant = [t for t in tickers if all(dominates(t, o) for o in tickers if o != t)]
    leads_on = {t: [m for m in common if metrics[m]["leader"] == t] for t in tickers}
    packet["pareto"] = {
        "dominant_company": dominant[0] if dominant else None,
        "dominated_by": dominated_by,
        "non_dominated": [t for t in tickers if not dominated_by[t]],
        "leads_on": leads_on,
    }

    # ---- stability grid
    scores = {m: _rank_score(vals[m]) for m in common}
    grid = {}
    equal_w = {m: 1.0 for m in common}
    grid["equal"] = _winner(scores, equal_w, tickers)[0]
    for m in common:
        w = dict(equal_w)
        w[m] = EMPHASIS_WEIGHT
        grid[f"emphasis_{m}"] = _winner(scores, w, tickers)[0]
    rng = random.Random(STABILITY_SEED)
    wins = {t: 0 for t in tickers}
    for _ in range(STABILITY_SAMPLES):
        w = {m: rng.expovariate(1.0) for m in common}       # uniform on the simplex
        wins[_winner(scores, w, tickers)[0]] += 1
    eq_winner, eq_scores = _winner(scores, equal_w, tickers)
    share = wins[eq_winner] / STABILITY_SAMPLES
    label = "stable" if share >= STABLE_SHARE else "mixed" if share >= MIXED_SHARE else "fragile"
    packet["stability"] = {
        "label": label, "equal_weight_winner": eq_winner,
        "equal_weight_scores": eq_scores, "grid": grid,
        "random_sweep": {"samples": STABILITY_SAMPLES, "seed": STABILITY_SEED,
                         "win_share": {t: wins[t] / STABILITY_SAMPLES for t in tickers}},
    }

    # ---- tradeoffs the verdict must mention: metrics where the equal-weight
    # winner is NOT the leader
    packet["tradeoffs"] = [
        {"metric": m, "leader": metrics[m]["leader"], "winner_value": vals[m][eq_winner],
         "leader_value": vals[m][metrics[m]["leader"]]}
        for m in common if metrics[m]["leader"] != eq_winner
    ]
    verdict_type = "dominant" if dominant else "tradeoff"
    packet["code_verdict"] = {"type": verdict_type,
                              "winner": dominant[0] if dominant else eq_winner}
    return packet
