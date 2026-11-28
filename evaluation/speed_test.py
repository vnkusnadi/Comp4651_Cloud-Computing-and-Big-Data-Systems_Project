"""
Person D speed test (the cloud computing part).

Part 1, answering (runs anywhere, no internet):
    python -m evaluation.speed_test query
  Times index build, fact sheet and verdict for every test question.

Part 2, data pipeline at more reports (needs internet access to sec.gov):
    SEC_USER_AGENT="YourName your.email@example.com" \
    python -m evaluation.speed_test ingest --years 2023 2024 2025 --workers 1 5
  Runs Person A's build step for every (company, year) one at a time
  (workers=1) and in parallel (workers=5), and records time per report,
  saved numbers per report, and total time. Person A's code is imported, not
  changed. The SEC asks every client to send a real contact in its User-Agent,
  so set SEC_USER_AGENT to your own name and email.

Results go to evaluation/results/speed_*.json.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def query_speed() -> None:
    from evaluation.run_eval import load_questions
    from src.analysis import category as cat
    from src.serve.engine import Engine

    t0 = time.perf_counter()
    e = Engine()
    build_s = time.perf_counter() - t0
    rows = []
    for q in load_questions("main"):
        r = cat.answer(q["question"], engine=e, llm=None)
        rows.append({"question_id": q["question_id"], **r.get("timing_ms", {})})
    pk = [r["packet"] for r in rows if "packet" in r]
    tot = [r["total"] for r in rows if "total" in r]
    out = {"num_units": len(e.units), "engine_build_s": round(build_s, 3),
           "index_stats": e.index_stats(),
           "packet_ms_median": round(statistics.median(pk), 1) if pk else None,
           "answer_ms_median": round(statistics.median(tot), 1) if tot else None,
           "answer_ms_max": round(max(tot), 1) if tot else None,
           "note": "verdict step is code-only here; with Claude add the API call time",
           "per_question": rows}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "speed_query.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "per_question"}, indent=2))


def _one_report(job, ua):
    """One (company, year) report. Module level so a separate process can run it."""
    sys.path.insert(0, str(REPO / "data_ingestion_and_processing"))
    import pipeline  # Person A's module, imported, not changed

    pipeline.SESSION.headers["User-Agent"] = ua
    t, y = job
    s = time.perf_counter()
    try:
        units = pipeline.build_evidence_units_for_company(t, fiscal_year=y)
        return {"ticker": t, "year": y, "units": len(units), "seconds": round(time.perf_counter() - s, 2)}
    except Exception as exc:
        return {"ticker": t, "year": y, "error": repr(exc)[:200], "seconds": round(time.perf_counter() - s, 2)}


def ingest_speed(years, workers_list, tickers=None, mode="process") -> None:
    """mode="process": each worker is its own process, like separate Airflow tasks.
    mode="thread": threads in one process; Python runs only one thread's parsing
    at a time, so this shows little or no speed-up."""
    sys.path.insert(0, str(REPO / "data_ingestion_and_processing"))
    import pipeline  # Person A's module

    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise SystemExit('Set SEC_USER_AGENT="Your Name your.email@example.com" first (SEC rule).')
    tickers = tickers or pipeline.TICKERS
    jobs = [(t, y) for y in years for t in tickers]
    Pool = ProcessPoolExecutor if mode == "process" else ThreadPoolExecutor

    runs = []
    for w in workers_list:
        s = time.perf_counter()
        with Pool(max_workers=w) as pool:
            res = [f.result() for f in as_completed([pool.submit(_one_report, j, ua) for j in jobs])]
        total = time.perf_counter() - s
        ok = [r for r in res if "units" in r]
        run = {"workers": w, "mode": mode, "reports": len(jobs), "succeeded": len(ok),
               "total_seconds": round(total, 2),
               "seconds_per_report_median": round(statistics.median([r["seconds"] for r in ok]), 2) if ok else None,
               "saved_numbers": sum(r["units"] for r in ok), "per_report": sorted(res, key=lambda r: (r["year"], r["ticker"]))}
        runs.append(run)
        print(f"{mode} workers={w}: {run['succeeded']}/{run['reports']} reports, {run['saved_numbers']} numbers, "
              f"{run['total_seconds']}s total")
        time.sleep(2)                      # be polite to sec.gov between runs
    (HERE / "results").mkdir(exist_ok=True)
    name = f"speed_ingest_{len(jobs)}reports_{mode}.json"
    (HERE / "results" / name).write_text(json.dumps(runs, indent=2), encoding="utf-8")
    print(f"Saved -> evaluation/results/{name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("query")
    ing = sub.add_parser("ingest")
    ing.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025])
    ing.add_argument("--workers", nargs="+", type=int, default=[1, 5])
    ing.add_argument("--tickers", nargs="+", default=None)
    ing.add_argument("--mode", choices=["process", "thread"], default="process")
    a = ap.parse_args()
    if a.cmd == "query":
        query_speed()
    else:
        ingest_speed(a.years, a.workers, a.tickers, a.mode)
