"""
Turn the pasted claude.ai answers into results/top_ai.jsonl for score.py.

    python -m evaluation.top_ai_import --seconds 95 120 80                      # strong model (Opus)
    python -m evaluation.top_ai_import --seconds 40 50 35 --system weak_ai      # weak model (Haiku)

1. Paste the JSON lines from all 3 chats into evaluation/<system>_raw.txt (top_ai_raw.txt or weak_ai_raw.txt) (any order;
   code fences and blank lines are fine).
2. --seconds: the stopwatch time of chat 1, 2 and 3. Each question gets its chat's
   time divided by the number of questions in that chat.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATS = ["Scale", "Profitability", "Operating efficiency"]


def main(seconds, system: str = "top_ai") -> None:
    qs = {r["question_id"]: r for r in csv.DictReader((HERE / "questions.csv").open(encoding="utf-8"))
          if r["set"] == "main"}
    per_q_ms = {}
    for cat, s in zip(CATS, seconds):
        ids = [q for q, r in qs.items() if r["category"] == cat]
        for q in ids:
            per_q_ms[q] = round(1000 * s / len(ids), 1)
    rows, bad = {}, []
    for line in (HERE / f"{system}_raw.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            bad.append(line[:80])
            continue
        qid = d.get("question_id")
        if qid not in qs:
            continue
        winner = d.get("winner")
        if isinstance(winner, str) and winner.lower() in ("null", "none", ""):
            winner = None
        nums = []
        for n in d.get("numbers") or []:
            try:
                page = int(re.sub(r"\D", "", str(n.get("page")))) if n.get("page") is not None else None
            except ValueError:
                page = None
            nums.append({"ticker": n.get("ticker"), "metric": n.get("metric"),
                         "value": n.get("value"), "page": page, "has_dimension": False})
        rows[qid] = {"question_id": qid, "system": system, "status": f"{system}_manual",
                     "verdict_type": d.get("verdict_type"), "winner": winner,
                     "answer_text": d.get("answer_text", ""), "numbers": nums, "claims": [],
                     "latency_ms": per_q_ms.get(qid)}
    out = HERE / "results" / f"{system}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for qid in sorted(rows):
            fh.write(json.dumps(rows[qid]) + "\n")
    missing = sorted(set(qs) - set(rows))
    print(f"Saved {len(rows)} answers -> {out}")
    if missing:
        print("Missing questions (ask claude.ai again for these):", ", ".join(missing))
    if bad:
        print(f"{len(bad)} lines were not valid JSON, e.g.: {bad[0]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", nargs=3, type=float, required=True,
                    help="stopwatch seconds for chat 1 (Scale), 2 (Profitability), 3 (Operating efficiency)")
    ap.add_argument("--system", default="top_ai", choices=["top_ai", "weak_ai"],
                    help="top_ai = strongest model (Opus); weak_ai = cheapest model (Haiku)")
    a = ap.parse_args()
    main(a.seconds, a.system)
