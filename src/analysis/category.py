"""
One call for a category question: parse -> packet (code) -> AI verdict -> 7-check gate.

    python -m src.analysis.category "Which company has the largest scale in the IT sector?"

Non-category questions are passed to Person C's Engine.answer unchanged.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Optional

from src.analysis.packet import build_packet
from src.analysis.synthesize import judge
from src.serve.engine import Engine

_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine()
    return _ENGINE


def answer(query: str, engine: Optional[Engine] = None, llm="default", use_gate: bool = True) -> Dict[str, Any]:
    t0 = time.perf_counter()
    eng = engine or get_engine()
    parsed = eng.parser.parse(query)
    if parsed.intent != "category_analysis" or not parsed.category:
        out = eng.answer(query)
        out["analysis"] = {"status": "not_a_category_question"}
        return out
    packet = build_packet(eng, parsed.category, parsed.companies, parsed.fiscal_year, query)
    t1 = time.perf_counter()
    result = judge(query, packet, llm=llm, use_gate=use_gate)
    return {
        "query": query, "parsed": parsed.to_dict(), "packet": packet,
        "status": result["status"], "verdict": result["verdict"], "gate": result["gate"],
        "attempts": result["attempts"],
        "timing_ms": {"packet": round((t1 - t0) * 1000, 2), "verdict": result["latency_ms"],
                      "total": round((time.perf_counter() - t0) * 1000, 2)},
    }


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Which company has the largest scale in the IT sector?"
    r = answer(q)
    print(json.dumps({k: r[k] for k in ("status", "verdict", "gate", "timing_ms") if k in r},
                     indent=2, default=str))
