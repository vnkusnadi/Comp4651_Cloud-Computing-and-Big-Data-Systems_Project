"""
Stage 5c: the 7-check gate. An AI verdict is shown ONLY if all 7 pass.

The AI returns a structured object (see synthesize.VERDICT_SCHEMA). Each check
compares it with the code-built packet:

  1 numbers_in_packet      every number the AI states is a packet value
  2 leaders_match_code     every "X leads on M" matches the packet leader
  3 tradeoffs_complete     every metric where the winner trails is mentioned
  4 verdict_fits_pareto    a dominant company must win; no "dominant" claim otherwise
  5 stability_disclosed    the stability label matches the packet
  6 coverage_disclosed     every company with missing metrics is named
  7 claims_cite_both       every comparison claim cites unit_ids for each company it names
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

REL_TOL = 0.005          # 0.5%: allows rounding like 36.15% -> 36.1%
NUM_RE = re.compile(r"(?<![A-Za-z_\d.])((?<![\d\w])-)?(\d[\d,]*\.?\d*)\s*(%|[BbMm](?:illion)?\b)?")
IGNORE_INTS = set(range(0, 13))      # small counts like "3 of 5", list numbering
YEARS = set(range(1990, 2101))


def _packet_numbers(packet: Dict[str, Any]) -> List[float]:
    """All numbers a faithful explanation could quote, in several display units."""
    out: List[float] = []
    for m in packet["metrics"].values():
        for v in m["values"].values():
            x = v["value"]
            out += [x, x * 100, x / 1e9, x / 1e6]
        if "median" in m:
            x = m["median"]
            out += [x, x * 100, x / 1e9, x / 1e6]
        for t, d in (m.get("delta_vs_median") or {}).items():
            out += [d, d * 100, d / 1e9, d / 1e6, abs(d), abs(d) * 100, abs(d) / 1e9, abs(d) / 1e6]
        g = m.get("gap_leader_vs_second")
        if g is not None:
            out += [g * 100, g]
    st = packet.get("stability") or {}
    for t, s in (st.get("random_sweep") or {}).get("win_share", {}).items():
        out += [s, s * 100]
    for c in packet.get("coverage", {}).values():
        out += [c["available"], c["of"], c["share"] * 100]
    # pairwise differences and ratios between companies on each metric
    for m in packet["metrics"].values():
        vals = [v["value"] for v in m["values"].values()]
        for a in vals:
            for b in vals:
                if a != b:
                    d = a - b
                    out += [d, abs(d), abs(d) * 100, abs(d) / 1e9, abs(d) / 1e6]
                    if b:
                        out += [a / b, (a / b - 1) * 100, abs(a / b - 1) * 100]
    return out


def _close(x: float, pool: List[float]) -> bool:
    """pool must be sorted. Tolerance: 0.5% relative plus 0.051 absolute (1-decimal rounding)."""
    import bisect
    tol = REL_TOL * abs(x) * 1.01 + 0.051
    i = bisect.bisect_left(pool, x - tol)
    while i < len(pool) and pool[i] <= x + tol:
        p = pool[i]
        if abs(x - p) <= REL_TOL * abs(p) + 0.051:
            return True
        i += 1
    return False


def extract_numbers(text: str) -> List[float]:
    nums = []
    for sign, raw, _suffix in NUM_RE.findall(text or ""):
        raw = raw.rstrip(".,")
        try:
            x = float(raw.replace(",", "")) * (-1 if sign else 1)
        except ValueError:
            continue
        if x in YEARS and "." not in raw:
            continue
        if x in IGNORE_INTS and "." not in raw:
            continue
        nums.append(x)
    return nums


def _all_text(v: Dict[str, Any]) -> str:
    parts = [v.get("summary", ""), v.get("weighting_rationale", ""),
             v.get("stability_disclosure", ""), v.get("missing_disclosure", "")]
    parts += [t.get("text", "") for t in v.get("tradeoffs", [])]
    parts += [c.get("text", "") for c in v.get("claims", [])]
    return "\n".join(p for p in parts if p)


def run_gate(verdict: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any = None):
        results.append({"check": name, "passed": bool(ok), "detail": detail})

    metrics = packet["metrics"]
    tickers: List[str] = packet["companies"]
    packet_ids: Set[str] = {uid for m in metrics.values() for v in m["values"].values()
                            for uid in v["unit_ids"]}

    # 1 numbers in packet ------------------------------------------------------
    pool = sorted(_packet_numbers(packet))
    stated = extract_numbers(_all_text(verdict))
    for c in verdict.get("claims", []):
        for x in c.get("values", []) or []:
            if isinstance(x, (int, float)):
                stated.append(float(x))
    bad = [x for x in stated if not _close(x, pool)]
    add("numbers_in_packet", not bad, {"ungrounded": bad[:10], "checked": len(stated)})

    # 2 leaders match code -----------------------------------------------------
    wrong = []
    for item in list(verdict.get("tradeoffs", [])) + list(verdict.get("claims", [])):
        m, lead = item.get("metric"), item.get("leader")
        if not m or not lead:
            continue
        if m not in metrics or metrics[m].get("highest") is None:
            wrong.append({"metric": m, "reason": "metric not comparable in packet"})
        elif metrics[m]["highest"] != lead:
            wrong.append({"metric": m, "said": lead, "code": metrics[m]["highest"]})
    add("leaders_match_code", not wrong, wrong)

    # 3 tradeoffs complete ------------------------------------------------------
    if packet.get("code_verdict", {}).get("type") == "profile":
        need = []
    else:
        need = [t["metric"] for t in packet.get("tradeoffs", [])
                if verdict.get("winner") in (None, packet["stability"]["equal_weight_winner"])]
        # if the AI picked another winner, it must mention where THAT company trails
        w = verdict.get("winner")
        if w and packet.get("stability") and w != packet["stability"]["equal_weight_winner"]:
            need = [m for m in packet["common_metrics"] if metrics[m]["leader"] != w]
    said = {t.get("metric") for t in verdict.get("tradeoffs", [])}
    missing_t = [m for m in need if m not in said]
    add("tradeoffs_complete", not missing_t, {"not_mentioned": missing_t})

    # 4 verdict fits Pareto ---------------------------------------------------
    cv = packet.get("code_verdict", {})
    vt, w = verdict.get("verdict_type"), verdict.get("winner")
    if cv.get("type") == "profile":
        ok = vt == "profile" and w is None
        why = "efficiency is a profile comparison; no single winner allowed"
    elif cv.get("type") == "insufficient":
        ok = vt == "insufficient"
        why = "packet has too little comparable data"
    elif cv.get("type") == "dominant":
        ok = w == cv["winner"] and vt in ("dominant", "winner")
        why = f"{cv['winner']} beats or ties every peer on every metric"
    else:
        ok = vt in ("tradeoff", "winner") and w in packet["pareto"]["non_dominated"]
        why = "no company dominates; winner must be non-dominated and not called dominant"
    add("verdict_fits_pareto", ok, {"rule": why, "said": {"type": vt, "winner": w}})

    # 5 stability disclosed ----------------------------------------------------
    if packet.get("stability"):
        lab = packet["stability"]["label"]
        ok = (verdict.get("stability_label") == lab
              and lab in (verdict.get("stability_disclosure") or "").lower())
        add("stability_disclosed", ok, {"packet_label": lab, "said": verdict.get("stability_label")})
    else:
        add("stability_disclosed", True, "not applicable (no single-winner category)")

    # 6 coverage disclosed -----------------------------------------------------
    text = (verdict.get("missing_disclosure") or "").upper()
    names = {"AAPL": "APPLE", "MSFT": "MICROSOFT", "NVDA": "NVIDIA", "AVGO": "BROADCOM", "CSCO": "CISCO"}
    gaps = sorted({m["ticker"] for m in packet.get("missing", [])})
    undisclosed = [t for t in gaps if t not in text and names.get(t, t) not in text]
    add("coverage_disclosed", not undisclosed, {"companies_with_gaps_not_named": undisclosed})

    # 7 claims cite both companies ----------------------------------------------
    problems = []
    for c in verdict.get("claims", []):
        ids = set(c.get("unit_ids") or [])
        unknown = ids - packet_ids
        if unknown:
            problems.append({"claim": c.get("text", "")[:80], "unknown_unit_ids": sorted(unknown)[:3]})
            continue
        for t in c.get("tickers") or []:
            m = c.get("metric")
            if m in metrics and t in metrics[m]["values"]:
                if not ids & set(metrics[m]["values"][t]["unit_ids"]):
                    problems.append({"claim": c.get("text", "")[:80], "no_source_for": t})
    if not verdict.get("claims"):
        problems.append("no claims with sources at all")
    add("claims_cite_both", not problems, problems)

    return {"passed": all(r["passed"] for r in results), "checks": results,
            "failed": [r["check"] for r in results if not r["passed"]]}
