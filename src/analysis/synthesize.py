"""
Stage 5c: AI verdict on top of the code-built packet, then the 7-check gate.

Flow for one category question:
  packet (code) -> AI writes a structured verdict -> gate (7 checks)
     -> fail: retry once, telling the AI which checks failed
     -> fail again (or no AI configured): show the code-only verdict.
An unchecked AI verdict is never returned as the answer.

The AI is Claude via the official Anthropic SDK. Set ANTHROPIC_API_KEY (or log
in with `ant auth login`). Pick the model with SECTORVIEW_MODEL; the cheapest,
claude-haiku-5-5, costs a few US cents for all 33 test questions. Set the effort with
SECTORVIEW_EFFORT (low, medium, high; ignored on claude-haiku-4-5). Without credentials, SECTORVIEW_LLM=none, or when
the call fails, the code-only verdict is returned and the response says so.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from src.analysis.gate import run_gate

DEFAULT_MODEL = os.environ.get("SECTORVIEW_MODEL", "claude-opus-5-5")
NAMES = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AVGO": "Broadcom", "CSCO": "Cisco"}

VERDICT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict_type", "winner", "summary", "tradeoffs", "weighting_rationale",
                 "stability_label", "stability_disclosure", "missing_disclosure", "claims"],
    "properties": {
        "verdict_type": {"type": "string", "enum": ["dominant", "winner", "tradeoff", "profile", "insufficient"]},
        "winner": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "tradeoffs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["metric", "leader", "text"],
            "properties": {"metric": {"type": "string"}, "leader": {"type": "string"},
                           "text": {"type": "string"}}}},
        "weighting_rationale": {"type": "string"},
        "stability_label": {"type": ["string", "null"]},
        "stability_disclosure": {"type": "string"},
        "missing_disclosure": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "metric", "tickers", "unit_ids", "values"],
            "properties": {"text": {"type": "string"}, "metric": {"type": "string"},
                           "leader": {"type": ["string", "null"]},
                           "tickers": {"type": "array", "items": {"type": "string"}},
                           "unit_ids": {"type": "array", "items": {"type": "string"}},
                           "values": {"type": "array", "items": {"type": "number"}}}}},
    },
}

SYSTEM_PROMPT = """You explain a fact sheet that code has already computed from SEC 10-K filings.
Your job is the part code cannot do: say which differences matter, describe the tradeoffs,
explain how you weighed the metrics, and give an overall verdict for the category.

Rules you must follow, because an automatic checker rejects answers that break them:
- Do not calculate anything new. Quote only numbers that appear in the fact sheet (you may round to 1 decimal).
- A metric's leader is the company the fact sheet lists as "highest". Never contradict it.
- If the fact sheet has a dominant_company, that company is the winner (verdict_type "dominant").
  If not, use "tradeoff" (or "winner" if one company clearly matters more), pick a non-dominated company,
  and list in "tradeoffs" every metric where your winner is NOT the leader.
- For a profile_comparison category, verdict_type is "profile" and winner is null: describe each company's profile.
- Copy the stability label exactly into stability_label and use that word in stability_disclosure.
- In missing_disclosure, name every company that has missing metrics and say which ones.
- Every claim lists the metric, the tickers it compares, and the unit_ids of the numbers it uses,
  copied from the fact sheet, for EVERY company it names.
- Plain English. No jargon. Short sentences."""


def compact_packet(packet: Dict[str, Any]) -> Dict[str, Any]:
    """The packet without bulky fields, as the AI sees it."""
    metrics = {}
    for m, e in packet["metrics"].items():
        metrics[m] = {
            "values": {t: {"value": v["value"], "unit": v["unit"], "unit_ids": v["unit_ids"],
                           "formula": v["formula"]} for t, v in e["values"].items()},
            "highest": e.get("highest"), "lowest": e.get("lowest"),
            "median": e.get("median"), "gap_leader_vs_second": e.get("gap_leader_vs_second"),
        }
    st = packet.get("stability") or {}
    return {
        "category": packet["category"], "direction": packet["direction"],
        "companies": {t: NAMES.get(t, t) for t in packet["companies"]},
        "fiscal_year_used": packet["fiscal_year_used"], "filing_dates": packet["filing_dates"],
        "metrics": metrics, "common_metrics": packet["common_metrics"],
        "missing": packet["missing"], "estimates": packet["estimates"],
        "pareto": packet.get("pareto"), "tradeoffs_vs_equal_weight_winner": packet.get("tradeoffs"),
        "stability": {"label": st.get("label"), "equal_weight_winner": st.get("equal_weight_winner"),
                      "win_share": (st.get("random_sweep") or {}).get("win_share"),
                      "grid": st.get("grid")} if st else None,
        "profile": packet.get("profile"),
    }


def build_messages(question: str, packet: Dict[str, Any],
                   failures: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    content = (f"Question: {question}\n\nFact sheet (JSON):\n"
               f"{json.dumps(compact_packet(packet), sort_keys=True, default=str)}")
    if failures:
        content += ("\n\nYour previous answer failed these automatic checks. Fix them:\n"
                    + json.dumps(failures, default=str))
    return [{"role": "user", "content": content}]


# ------------------------------------------------------------------ LLM client
LLMFn = Callable[[str, List[Dict[str, Any]]], Dict[str, Any]]

# Token use of every Claude call, read by evaluation/run_eval.py to price each answer.
USAGE: List[Dict[str, Any]] = []


def claude_llm(model: str = DEFAULT_MODEL) -> LLMFn:
    """Returns a function (system, messages) -> verdict dict, backed by Claude."""
    import anthropic

    client = anthropic.Anthropic()

    def call(system: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        out_cfg: Dict[str, Any] = {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}}
        if not model.startswith("claude-haiku-4"):
            # Haiku 4.5 rejects the effort setting; newer models take it.
            out_cfg["effort"] = os.environ.get("SECTORVIEW_EFFORT", "medium")
        params = dict(model=model, max_tokens=16000, system=system, messages=messages,
                      output_config=out_cfg)
        if model.startswith("claude-haiku"):
            # Haiku has no server-side refusal fallback; a refusal falls back to code-only below.
            resp = client.messages.create(**params)
        else:
            resp = client.beta.messages.create(**params, betas=["server-side-fallback-2026-07-01"],
                                               fallbacks="default")
        u = getattr(resp, "usage", None)
        if u is not None:
            USAGE.append({"model": model, "input_tokens": getattr(u, "input_tokens", 0) or 0,
                          "output_tokens": getattr(u, "output_tokens", 0) or 0})
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return json.loads(text)

    return call


def default_llm() -> Optional[LLMFn]:
    if os.environ.get("SECTORVIEW_LLM", "claude").lower() in ("none", "off", "0"):
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.path.isdir(os.path.expanduser("~/.config/anthropic"))):
        return None
    try:
        return claude_llm()
    except ImportError:            # pip install anthropic
        return None


# ------------------------------------------------------------------ code-only verdict
def code_only_verdict(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic verdict, used as the baseline and as the fallback.
    Built to pass the same 7 checks."""
    cv = packet["code_verdict"]
    metrics = packet["metrics"]
    st = packet.get("stability")
    claims, tradeoffs = [], []
    for m in packet["common_metrics"]:
        e = metrics[m]
        ts = e["ranking_high_to_low"]
        ids = [uid for t in ts for uid in e["values"][t]["unit_ids"]]
        claims.append({"text": f"{m}: highest {NAMES.get(e['highest'], e['highest'])}, "
                               f"lowest {NAMES.get(e['lowest'], e['lowest'])}.",
                       "metric": m, "leader": e["highest"], "tickers": ts, "unit_ids": ids, "values": []})
    for t in packet.get("tradeoffs") or []:
        tradeoffs.append({"metric": t["metric"], "leader": t["leader"],
                          "text": f"{NAMES.get(t['leader'], t['leader'])} leads on {t['metric']}."})
    gaps: Dict[str, List[str]] = {}
    for g in packet["missing"]:
        gaps.setdefault(g["ticker"], []).append(g["metric"])
    missing_text = "; ".join(f"{NAMES.get(t, t)} is missing {', '.join(ms)}" for t, ms in gaps.items()) \
        or "No metrics are missing."
    if cv["type"] == "profile":
        summary = "Operating efficiency is compared as a profile, not ranked: " + "; ".join(
            f"{NAMES.get(t, t)} is highest on {', '.join(p['highest_on']) or 'nothing'}"
            for t, p in packet["profile"].items()) + "."
        return {"verdict_type": "profile", "winner": None, "summary": summary, "tradeoffs": [],
                "weighting_rationale": "No weighting: higher is not always better for spending ratios.",
                "stability_label": None, "stability_disclosure": "Not applicable for a profile.",
                "missing_disclosure": missing_text, "claims": claims}
    if cv["type"] == "insufficient":
        return {"verdict_type": "insufficient", "winner": None,
                "summary": "Not enough comparable numbers to judge this category.", "tradeoffs": [],
                "weighting_rationale": "", "stability_label": None, "stability_disclosure": "",
                "missing_disclosure": missing_text, "claims": claims}
    w = cv["winner"]
    lab = st["label"]
    summary = (f"{NAMES.get(w, w)} {'leads on every metric' if cv['type'] == 'dominant' else 'comes out ahead with equal weights'}"
               f" in {packet['category']}.")
    return {"verdict_type": cv["type"], "winner": w, "summary": summary, "tradeoffs": tradeoffs,
            "weighting_rationale": "Every comparable metric counts equally after scaling each to 0-1.",
            "stability_label": lab,
            "stability_disclosure": f"Verdict stability: {lab} under random reweighting.",
            "missing_disclosure": missing_text, "claims": claims}


# ------------------------------------------------------------------ main entry
def judge(question: str, packet: Dict[str, Any], llm: Optional[LLMFn] = "default",
          max_attempts: int = 2, use_gate: bool = True) -> Dict[str, Any]:
    """Returns {status, verdict, gate, attempts, latency_ms}.
    status: ai_verified | code_only_no_llm | code_only_fallback | ai_unchecked (gate off, ablation only)."""
    t0 = time.perf_counter()
    if llm == "default":
        llm = default_llm()
    attempts: List[Dict[str, Any]] = []
    if llm is None:
        v = code_only_verdict(packet)
        return {"status": "code_only_no_llm", "verdict": v, "gate": run_gate(v, packet),
                "attempts": attempts, "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
    failures = None
    for i in range(max_attempts):
        try:
            v = llm(SYSTEM_PROMPT, build_messages(question, packet, failures))
        except Exception as exc:                       # network, refusal, bad JSON
            attempts.append({"attempt": i + 1, "error": repr(exc)[:300]})
            continue
        g = run_gate(v, packet)
        attempts.append({"attempt": i + 1, "verdict": v, "gate": g})
        if not use_gate:
            return {"status": "ai_unchecked", "verdict": v, "gate": g, "attempts": attempts,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
        if g["passed"]:
            return {"status": "ai_verified", "verdict": v, "gate": g, "attempts": attempts,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
        failures = [c for c in g["checks"] if not c["passed"]]
    v = code_only_verdict(packet)
    return {"status": "code_only_fallback", "verdict": v, "gate": run_gate(v, packet),
            "attempts": attempts, "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
