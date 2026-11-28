"""
Person D: run every test question through one system and save its answers.

    python -m evaluation.run_eval --system code_only
    python -m evaluation.run_eval --system ours            # needs ANTHROPIC_API_KEY
    python -m evaluation.run_eval --system ours_no_gate    # switch-off test: 7 checks off
    python -m evaluation.run_eval --system ours --set everyday_wording

Systems that run outside this repo (top AI model, flat RAG) write the same
answer format by hand or with their own script; see evaluation/README.md.

Answer format (one JSON object per line in evaluation/results/<system>.jsonl):
  question_id, system, status, verdict_type, winner, answer_text,
  numbers: [{ticker, metric, value, unit, page, row_label, has_dimension, unit_ids}],
  claims:  [{text, metric, tickers, cites_all_tickers}],
  gate_passed, gate_failed, latency_ms
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from src.analysis import category as cat
from src.analysis import synthesize as synth
from src.analysis.synthesize import default_llm
from src.serve.engine import Engine

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def load_questions(which: str = "main") -> List[Dict[str, str]]:
    rows = list(csv.DictReader((HERE / "questions.csv").open(encoding="utf-8")))
    return [r for r in rows if which == "all" or r["set"] == which]


def _numbers_from_packet(packet: Dict[str, Any], engine: Engine) -> List[Dict[str, Any]]:
    out = []
    for m in packet["common_metrics"]:
        for t, v in packet["metrics"][m]["values"].items():
            src = v.get("source") or {}
            u = engine.by_id.get(v["unit_ids"][0], {}) if len(v["unit_ids"]) == 1 else {}
            out.append({"ticker": t, "metric": m, "value": v["value"], "unit": v["unit"],
                        "page": src.get("page"), "row_label": src.get("row_label"),
                        "has_dimension": bool(u.get("dimensions")) if u else False,
                        "unit_ids": v["unit_ids"], "inputs": v["inputs"]})
    return out


def _claims(verdict: Dict[str, Any], packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for c in verdict.get("claims", []):
        ids = set(c.get("unit_ids") or [])
        m = c.get("metric")
        cites = all(m in packet["metrics"] and t in packet["metrics"][m]["values"]
                    and ids & set(packet["metrics"][m]["values"][t]["unit_ids"])
                    for t in c.get("tickers") or [])
        out.append({"text": c.get("text"), "metric": m, "tickers": c.get("tickers"),
                    "cites_all_tickers": bool(cites and c.get("tickers"))})
    return out


def run(system: str, which: str = "main", out_path: Path | None = None) -> Path:
    engine = Engine()
    if system == "code_only":
        llm, use_gate = None, True
    elif system in ("ours", "ours_no_gate"):
        llm, use_gate = default_llm(), system == "ours"
        if llm is None:
            raise SystemExit("No Claude credentials found. Set ANTHROPIC_API_KEY first "
                             "(see evaluation/README.md, step 'Get an API key').")
    else:
        raise SystemExit(f"unknown system {system}")
    RESULTS.mkdir(exist_ok=True)
    out_path = out_path or RESULTS / f"{system}{'' if which == 'main' else '_' + which}.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for q in load_questions(which):
            synth.USAGE.clear()
            t0 = time.perf_counter()
            r = cat.answer(q["question"], engine=engine, llm=llm, use_gate=use_gate)
            ms = round((time.perf_counter() - t0) * 1000, 2)
            if "packet" not in r:            # parser did not see a category question
                row = {"question_id": q["question_id"], "system": system,
                       "status": "not_understood_as_category_question",
                       "understood_category": None, "understood_companies": None,
                       "verdict_type": None, "winner": None,
                       "answer_text": r.get("conclusion", ""), "numbers": [], "claims": [],
                       "gate_passed": None, "gate_failed": [], "latency_ms": ms}
            else:
                v = r["verdict"]
                row = {"question_id": q["question_id"], "system": system, "status": r["status"],
                       "understood_category": r["packet"]["category"],
                       "understood_companies": r["packet"]["companies"],
                       "verdict_type": v.get("verdict_type"), "winner": v.get("winner"),
                       "answer_text": v.get("summary", ""),
                       "numbers": _numbers_from_packet(r["packet"], engine),
                       "claims": _claims(v, r["packet"]),
                       "gate_passed": r["gate"]["passed"], "gate_failed": r["gate"]["failed"],
                       "ai_attempts": len(r.get("attempts", [])),
                       "ai_errors": [x["error"] for x in r.get("attempts", []) if x.get("error")],
                       "latency_ms": ms, "timing_ms": r["timing_ms"],
                       "usage": {"calls": len(synth.USAGE),
                                 "input_tokens": sum(u["input_tokens"] for u in synth.USAGE),
                                 "output_tokens": sum(u["output_tokens"] for u in synth.USAGE),
                                 "model": synth.USAGE[0]["model"] if synth.USAGE else None}}
            fh.write(json.dumps(row, default=str) + "\n")
            print(f"{q['question_id']}  {row['status']:<32} winner={row['winner']}")
            if row.get("ai_errors"):
                print(f"     AI call failed: {row['ai_errors'][0][:200]}")
    print(f"\nSaved -> {out_path}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default="code_only", choices=["code_only", "ours", "ours_no_gate"])
    ap.add_argument("--set", default="main", choices=["main", "everyday_wording", "blind_everyday", "all"])
    a = ap.parse_args()
    run(a.system, a.set)
