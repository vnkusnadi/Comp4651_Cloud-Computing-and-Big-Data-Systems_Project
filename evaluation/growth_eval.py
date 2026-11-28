"""
Multi-year test: 10 growth questions (fiscal 2021 to fiscal 2025), each needing
two 10-Ks per company.

    python -m evaluation.growth_eval            # run our code, then score every growth result file
    python -m evaluation.growth_eval import top_ai 140   # paste claude.ai answers into
                                                         # evaluation/top_ai_growth_raw.txt first

Scores each system in results/*_growth.jsonl against answer_key_growth.csv:
right winner, numbers within 1% of the key, cited page matches, time per answer.
The winner key is computed from the key values (higher growth wins).
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
TOL = 0.01


def load_key():
    key = {}
    for r in csv.DictReader((HERE / "answer_key_growth.csv").open(encoding="utf-8")):
        key[(r["ticker"], r["metric"], int(r["fiscal_year"]))] = {
            "value": float(r["value_in_10k"].replace(",", "")) * 1e6, "page": r["page_in_10k"]}
    return key


def questions():
    return [q for q in csv.DictReader((HERE / "questions.csv").open(encoding="utf-8")) if q["set"] == "growth"]


def expected_winner(q, key):
    metric = "Revenue_total" if "revenue" in q["question"].lower() else "NetIncomeLoss"
    g = {t: key[(t, metric, 2025)]["value"] / key[(t, metric, 2021)]["value"] - 1 for t in q["companies"].split("|")}
    return max(g, key=g.get), metric


def run_ours():
    from src.analysis.growth import MULTI_YEAR_UNITS, answer_growth
    from src.serve.engine import Engine

    e = Engine(units_path=MULTI_YEAR_UNITS)
    out = RESULTS / "code_only_growth.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for q in questions():
            t0 = time.perf_counter()
            r = answer_growth(q["question"], e)
            ms = round((time.perf_counter() - t0) * 1000, 2)
            nums = []
            for t, row in (r.get("per_company") or {}).items():
                for side in ("start", "end"):
                    v = row[side]
                    nums.append({"ticker": t, "metric": r["parsed"]["metric"], "fiscal_year": v["fiscal_year"],
                                 "value": v["value"], "page": v["page"], "unit_id": v["unit_id"]})
            fh.write(json.dumps({"question_id": q["question_id"], "system": "code_only", "status": r["status"],
                                 "winner": r.get("winner"), "answer_text": r.get("answer_text", ""),
                                 "numbers": nums, "latency_ms": ms}) + "\n")
    return out


def score(path: Path, key):
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    qs = {q["question_id"]: q for q in questions()}
    win_ok = num_ok = num_n = pg_ok = pg_n = 0
    lat = []
    for r in rows:
        q = qs.get(r["question_id"])
        if not q:
            continue
        w, _ = expected_winner(q, key)
        win_ok += r.get("winner") == w
        for n in r.get("numbers", []):
            k = key.get((n.get("ticker"), n.get("metric"), int(n.get("fiscal_year") or 0)))
            if not k or n.get("value") is None:
                continue
            num_n += 1
            num_ok += abs(float(n["value"]) - k["value"]) <= TOL * k["value"]
            if n.get("page") is not None:
                pg_n += 1
                pg_ok += str(n["page"]) == k["page"]
        if r.get("latency_ms") is not None:
            lat.append(float(r["latency_ms"]))
    pct = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731
    return {"system": rows[0]["system"] if rows else path.stem, "questions": len(rows),
            "winner_right_%": pct(win_ok, len(qs)), "numbers_right_%": pct(num_ok, num_n), "numbers_checked": num_n,
            "pages_right_%": pct(pg_ok, pg_n), "median_latency_ms": round(statistics.median(lat), 1) if lat else None}


def main():
    key = load_key()
    run_ours()
    summaries = [score(p, key) for p in sorted(RESULTS.glob("*_growth.jsonl"))]
    cols = list(summaries[0].keys())
    print("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols))
    for s in summaries:
        print("| " + " | ".join(str(s[c]) for c in cols) + " |")
    (RESULTS / "scores_growth.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")


def import_ai(system: str, seconds: float) -> None:
    """claude.ai answers (JSON lines) -> results/<system>_growth.jsonl."""
    raw = (HERE / f"{system}_growth_raw.txt").read_text(encoding="utf-8")
    ids = {q["question_id"] for q in questions()}
    rows = {}
    for line in raw.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("question_id") in ids:
            rows[d["question_id"]] = {"question_id": d["question_id"], "system": system, "status": f"{system}_manual",
                                      "winner": d.get("winner"), "answer_text": d.get("answer_text", ""),
                                      "numbers": d.get("numbers") or [], "latency_ms": round(1000 * seconds / len(ids), 1)}
    with (RESULTS / f"{system}_growth.jsonl").open("w", encoding="utf-8") as fh:
        for q in sorted(rows):
            fh.write(json.dumps(rows[q]) + "\n")
    print(f"Saved {len(rows)} answers; missing: {sorted(ids - set(rows)) or 'none'}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "import":
        import_ai(sys.argv[2], float(sys.argv[3]))
    else:
        main()
