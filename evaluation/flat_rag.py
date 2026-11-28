"""
Baseline: plain AI search ("flat RAG").

Cut each 10-K into text chunks, find the chunks most similar to the question
(BM25 keyword search, Person C's index), and let Claude answer from those
chunks alone. No tables kept, no dictionary, no division check, no 7 checks.
This is how a typical "chat with your PDF" tool works.

Needs:
  1. the 10-K files on disk:  python scripts/download_filings.py   (needs sec.gov access)
  2. Claude credentials:      ANTHROPIC_API_KEY

    python -m evaluation.flat_rag            # writes evaluation/results/flat_rag.jsonl
"""

from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List

from evaluation.run_eval import load_questions
from src.analysis.synthesize import DEFAULT_MODEL
from src.index.bm25 import BM25Index

HERE = Path(__file__).resolve().parent
STAGING = HERE.parent / "data_ingestion_and_processing" / "staging"
CHUNK_WORDS = 300
TOP_K = 8

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict_type", "winner", "answer_text", "numbers"],
    "properties": {
        "verdict_type": {"type": "string", "enum": ["winner", "tradeoff", "profile", "insufficient"]},
        "winner": {"type": ["string", "null"]},
        "answer_text": {"type": "string"},
        "numbers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["ticker", "metric", "value", "page"],
            "properties": {"ticker": {"type": "string"}, "metric": {"type": "string"},
                           "value": {"type": "number"}, "page": {"type": ["integer", "null"]}}}},
    },
}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, d):
        if d.strip():
            self.parts.append(d.strip())


def chunks_for(path: Path, ticker: str) -> List[Dict[str, Any]]:
    p = _Text()
    p.feed(path.read_text(encoding="utf-8", errors="ignore"))
    words = " ".join(p.parts).split()
    return [{"unit_id": f"{ticker}-{i}", "ticker": ticker,
             "text": " ".join(words[i:i + CHUNK_WORDS])} for i in range(0, len(words), CHUNK_WORDS)]


def main() -> None:
    import anthropic

    files = {f.name.split("_")[0]: f for f in STAGING.glob("*_*.htm*")}
    if not files:
        raise SystemExit(f"No 10-K files in {STAGING}. Run: python scripts/download_filings.py")
    chunks = [c for t, f in files.items() for c in chunks_for(f, t)]
    index = BM25Index(chunks, [c["text"] for c in chunks])
    client = anthropic.Anthropic()
    out = HERE / "results" / "flat_rag.jsonl"
    out.parent.mkdir(exist_ok=True)
    METRICS = ("Use these metric names: Revenue_total, GrossProfit, OperatingIncomeLoss, NetIncomeLoss, "
               "Assets, GrossMargin, OperatingMargin, NetMargin, ReturnOnAssets, ReturnOnEquity, "
               "R&D_expense_to_revenue, SalesAndMarketing_to_revenue, TotalOperatingExpense_to_revenue. "
               "Give money in USD (not millions). Give ratios as decimals (0.36, not 36%).")
    with out.open("w", encoding="utf-8") as fh:
        for q in load_questions("main"):
            t0 = time.perf_counter()
            allowed = {i for i, c in enumerate(chunks) if c["ticker"] in q["companies"].split("|")}
            hits = index.search(q["question"], k=TOP_K, allowed=allowed)
            ctx = "\n\n".join(f"[{chunks[i]['ticker']} chunk {chunks[i]['unit_id']}]\n{chunks[i]['text']}"
                              for i, _ in hits)
            resp = client.messages.create(
                model=DEFAULT_MODEL, max_tokens=16000,
                system="Answer the question using only the report excerpts given. " + METRICS,
                messages=[{"role": "user", "content": f"{q['question']}\n\nExcerpts:\n{ctx}"}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                a = json.loads(text)
            except json.JSONDecodeError:
                a = {"verdict_type": "insufficient", "winner": None, "answer_text": text, "numbers": []}
            row = {"question_id": q["question_id"], "system": "flat_rag", "status": "answered",
                   "verdict_type": a["verdict_type"], "winner": a["winner"], "answer_text": a["answer_text"],
                   "numbers": [{**n, "has_dimension": False} for n in a["numbers"]],
                   "claims": [], "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
            fh.write(json.dumps(row) + "\n")
            print(q["question_id"], row["winner"])
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
