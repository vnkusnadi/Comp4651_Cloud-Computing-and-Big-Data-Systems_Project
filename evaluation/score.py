"""
Person D: score saved answers against the hand-made answer key.

    python -m evaluation.score                       # every results/*.jsonl
    python -m evaluation.score results/ours.jsonl

The six measures (slide 19):
  1 answer_accuracy      picked the right company (or correctly gave no winner; for a
                         tradeoff, "tradeoff, no single winner" also counts as right)
  2 number_accuracy      reported numbers within 1% of the hand-checked value
  3 right_source         cited page matches the hand-checked page, and not a division row
  4 grounded_numbers     share of numbers that match a hand-checked value (no made-up numbers)
  5 claims_with_sources  share of comparison claims that cite every company they name
  6 latency_ms           median time per answer

Derived metrics (margins, ratios) are recomputed from YOUR hand values with the
same schema formula, so the key stays hand-made.

Rule: a right answer whose numbers came from the wrong row still counts as wrong
in "strict_accuracy" (answer right AND every number right AND every source right).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.util.formula import eval_formula, _formula_names, _safe_formula
from src.util.yaml import load as load_yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCHEMA = REPO / "configs" / "sectors" / "information_technology.yaml"
NUM_TOL = 0.01
MILLION_METRICS_EXCLUDED = {"EarningsPerShareDiluted", "EarningsPerShareBasic"}


def _f(x: str) -> Optional[float]:
    x = (x or "").replace(",", "").replace("$", "").strip()
    if not x:
        return None
    neg = x.startswith("(") and x.endswith(")")
    x = x.strip("()")
    try:
        v = float(x)
    except ValueError:
        return None
    return -v if neg else v


def load_key() -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    values: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in csv.DictReader((HERE / "answer_key_values.csv").open(encoding="utf-8")):
        v = _f(r["value_in_10k"])
        if v is None:
            continue
        if r["metric"] not in MILLION_METRICS_EXCLUDED and "million" in (r["unit"] or "").lower():
            v *= 1e6
        pages = {p.strip() for p in (r["page_in_10k"] or "").replace(";", "|").split("|") if p.strip()}
        values[(r["ticker"], r["metric"])] = {"value": v, "pages": pages, "row": r["row_label_in_10k"]}
    verdicts = {}
    for r in csv.DictReader((HERE / "answer_key_verdicts.csv").open(encoding="utf-8")):
        if r["expected_verdict_type"].strip() or r["acceptable_winners"].strip():
            verdicts[r["question_id"]] = {
                "type": r["expected_verdict_type"].strip(),
                "winners": {w.strip() for w in r["acceptable_winners"].split("|") if w.strip()}}
    return values, verdicts


def key_value(values, ticker: str, metric: str, schema) -> Optional[float]:
    if (ticker, metric) in values:
        return values[(ticker, metric)]["value"]
    d = next((d for d in schema["derived_metrics"] if d["canonical_name"] == metric), None)
    if not d:
        return None
    env = {}
    for n in _formula_names(_safe_formula(d["formula"])):
        real = n.replace("_and_", "&")
        x = key_value(values, ticker, real, schema)
        if x is None:
            return None
        env[real] = x
    try:
        return eval_formula(d["formula"], env)
    except ZeroDivisionError:
        return None


def score_file(path: Path, values, verdicts, schema) -> Dict[str, Any]:
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    ans_ok = ans_n = num_ok = num_n = src_ok = src_n = grounded = stated = 0
    claims_ok = claims_n = strict_ok = 0
    rank_ok = rank_n = 0
    lat: List[float] = []
    per_q = []
    for r in rows:
        qid = r["question_id"]
        q_num_ok = q_src_ok = True
        # 1 answer accuracy
        exp = verdicts.get(qid)
        a_ok = None
        if exp:
            ans_n += 1
            if exp["type"] == "profile":
                a_ok = r.get("winner") in (None, "") and r.get("verdict_type") == "profile"
            elif exp["type"] == "tradeoff" and r.get("verdict_type") == "tradeoff" and r.get("winner") in (None, ""):
                a_ok = True          # "no single winner" is a fair answer to a tradeoff question
            else:
                a_ok = (r.get("winner") in exp["winners"]) if exp["winners"] else \
                       (r.get("verdict_type") == exp["type"])
            ans_ok += bool(a_ok)
            if exp["type"] != "profile":
                rank_n += 1
                rank_ok += bool(a_ok)
        # 2-4 numbers and sources
        for n in r.get("numbers", []):
            kv = key_value(values, n["ticker"], n["metric"], schema)
            if n.get("value") is None:
                continue
            stated += 1
            if kv is None:
                continue
            num_n += 1
            good = abs(float(n["value"]) - kv) <= NUM_TOL * abs(kv) if kv else float(n["value"]) == 0
            num_ok += good
            grounded += good
            q_num_ok &= good
            key = values.get((n["ticker"], n["metric"]))
            if key and key["pages"] and n.get("page") is not None:
                src_n += 1
                s_good = str(n["page"]) in key["pages"] and not n.get("has_dimension")
                src_ok += s_good
                q_src_ok &= s_good
        # 5 claims
        for c in r.get("claims", []):
            claims_n += 1
            claims_ok += bool(c.get("cites_all_tickers"))
        if r.get("latency_ms") is not None:
            lat.append(float(r["latency_ms"]))
        strict = bool(a_ok) and q_num_ok and q_src_ok
        strict_ok += strict
        per_q.append({"question_id": qid, "answer_correct": a_ok, "numbers_correct": q_num_ok,
                      "sources_correct": q_src_ok, "strict_correct": strict if exp else None,
                      "status": r.get("status")})

    def pct(a, b):
        return round(100 * a / b, 1) if b else None

    return {
        "system": rows[0]["system"] if rows else path.stem, "file": path.name, "questions": len(rows),
        "answer_accuracy_%": pct(ans_ok, ans_n), "answer_key_questions": ans_n,
        "answer_accuracy_scale_profitability_%": pct(rank_ok, rank_n),
        "strict_accuracy_%": pct(strict_ok, ans_n),
        "number_accuracy_%": pct(num_ok, num_n), "numbers_checked": num_n,
        "right_source_%": pct(src_ok, src_n), "sources_checked": src_n,
        "grounded_numbers_%": pct(grounded, num_n), "numbers_stated": stated,
        "claims_with_sources_%": pct(claims_ok, claims_n), "claims": claims_n,
        "median_latency_ms": round(statistics.median(lat), 1) if lat else None,
        "not_understood": sum(1 for r in rows if r.get("status") == "not_understood_as_category_question"),
        "ai_fell_back_to_code": sum(1 for r in rows if r.get("status") == "code_only_fallback"),
        "per_question": per_q,
    }


def main(paths: List[str]) -> None:
    schema = load_yaml(SCHEMA)
    values, verdicts = load_key()
    if not values:
        print("WARNING: answer_key_values.csv has no values yet. Number scores will be empty.")
    if not verdicts:
        print("WARNING: answer_key_verdicts.csv has no expected answers yet. Answer accuracy will be empty.")
    files = [Path(p) for p in paths] or sorted(p for p in (HERE / "results").glob("*.jsonl")
                                               if not p.stem.endswith("_growth"))   # growth: growth_eval.py
    summaries = [score_file(p, values, verdicts, schema) for p in files]
    cols = ["system", "questions", "answer_key_questions", "answer_accuracy_%",
            "answer_accuracy_scale_profitability_%", "strict_accuracy_%", "number_accuracy_%",
            "right_source_%", "grounded_numbers_%", "claims_with_sources_%", "median_latency_ms",
            "not_understood", "ai_fell_back_to_code"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for s in summaries:
        print("| " + " | ".join(str(s.get(c)) for c in cols) + " |")
    out = HERE / "results" / "scores.json"
    out.write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")
    print(f"\nFull scores -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
