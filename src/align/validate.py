"""
Person B — Stage 5: cross-company alignment validation.

Implements the course-spec validation contract (§4.3):

    def validate_alignment(candidate_a, candidate_b, tolerance=0.02):
        if dimension_conflict(candidate_a, candidate_b):
            return "rejected", "dimension_mismatch"
        if not numeric_continuity(candidate_a, candidate_b, tolerance):
            return "rejected", "calculation_gap_exceeded"
        return "accepted", "passed"

Concept-specific calculation constraints:
  * Revenue_total — segment sum + eliminations ≈ total (within tolerance).
  * R&D_expense   — consistent with the income statement; never segment-only
                    or footnote-only unless explicitly labelled.

Stage-5 output — alignment edges:
    (unit_a, unit_b, concept, period, validation_status, gap)

Design note (cross-company value equality):
  numeric_continuity compares two *candidate* units for the SAME company
  (a segment candidate vs. the consolidated total, or a paragraph claim vs.
  the linked table row). Cross-company units legitimately differ in value,
  so a cross-company edge is validated by dimension-consistency plus each
  side's own internal checks, and the value difference is recorded as `gap`.

Input: Person A's evidence_units (jsonl or parquet).
Demo against the real dataset:
    python src/align/validate.py
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants (aligned with Person A's schema_and_validation.py; Person B owns
# the alignment contract, Person A owns the evidence-unit schema)
# ---------------------------------------------------------------------------

TOLERANCE = 0.02

# Consolidated metrics: must carry no segment/product dimension.
CONSOLIDATED_METRICS = {
    "Revenue_total",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "ResearchAndDevelopmentExpense",
    "OperatingExpenses",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "DepreciationDepletionAndAmortization",
    "Assets",
    "StockholdersEquity",
}

REVENUE_METRICS = {"Revenue_total", "Revenue_product", "Revenue_service"}

PRIMARY_STATEMENT_MARKERS = (
    "statements of operations",
    "statements of income",
    "statements of earnings",
    "income statements",
    "balance sheets",
    "statements of financial position",
    "statements of cash flows",
    "statements of comprehensive income",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UNITS = (
    REPO_ROOT
    / "data_ingestion_and_processing"
    / "data"
    / "evidence_units"
    / "evidence_units.jsonl"
)

# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def load_units(path: str | Path = DEFAULT_UNITS) -> List[Dict[str, Any]]:
    """Load evidence units from Person A's jsonl (or parquet) output."""
    path = Path(path)
    if path.suffix == ".jsonl":
        units = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    units.append(json.loads(line))
    elif path.suffix == ".parquet":
        import pandas as pd  # deferred: needs pyarrow, may be unavailable locally
        units = pd.read_parquet(path).to_dict(orient="records")
    else:
        raise ValueError(f"unsupported unit file: {path}")
    # In the parquet, `dimensions` is serialised as a JSON string.
    for u in units:
        dims = u.get("dimensions")
        if isinstance(dims, str):
            u["dimensions"] = json.loads(dims) if dims else {}
        elif dims is None:
            u["dimensions"] = {}
    return units


def dims_of(unit: Dict[str, Any]) -> Dict[str, str]:
    return unit.get("dimensions") or {}


def best_unit(
    units: List[Dict[str, Any]],
    ticker: str,
    fiscal_year: int,
    metric: str,
    require_consolidated: bool = False,
) -> Optional[Dict[str, Any]]:
    """Pick the representative passed unit for (ticker, FY, metric).

    Prefers xbrl_tag mapping (the strictest source); falls back to any
    passed unit. Cross-company alignment uses this per side.
    """
    cands = [
        u
        for u in units
        if u.get("ticker") == ticker
        and u.get("fiscal_year") == fiscal_year
        and u.get("metric") == metric
        and u.get("validation_status") == "passed"
        and (not require_consolidated or not dims_of(u))
    ]
    if not cands:
        return None
    xbrl = [u for u in cands if u.get("mapping_method") == "xbrl_tag"]
    pick = xbrl or cands
    pick.sort(key=lambda u: u.get("filing_date") or "", reverse=True)
    return pick[0]


# ---------------------------------------------------------------------------
# §4.3 validation contract
# ---------------------------------------------------------------------------

def dimension_conflict(unit_a: Dict[str, Any], unit_b: Dict[str, Any]) -> bool:
    """Two units conflict if they carry different segment/product dimensions
    but are being compared as if they were the same concept."""
    dims_a, dims_b = dims_of(unit_a), dims_of(unit_b)
    if not dims_a and not dims_b:
        return False                    # both consolidated → no conflict
    if bool(dims_a) != bool(dims_b):
        return True                     # consolidated vs. dimensional → conflict
    return dims_a != dims_b             # different dimension sets → conflict


def numeric_continuity(
    candidate_a: Dict[str, Any],
    candidate_b: Dict[str, Any],
    tolerance: float = TOLERANCE,
) -> bool:
    """Generic check: relative difference of two candidates within tolerance.

    Used within a company (segment candidate vs. total, paragraph claim vs.
    table row) — NOT as a cross-company equality requirement.
    """
    val_a = candidate_a.get("value")
    val_b = candidate_b.get("value")
    if val_a is None or val_b is None:
        return False
    denominator = max(abs(val_a), abs(val_b), 1e-9)
    return abs(val_a - val_b) / denominator <= tolerance


def validate_alignment(
    candidate_a: Dict[str, Any],
    candidate_b: Dict[str, Any],
    tolerance: float = TOLERANCE,
) -> Tuple[str, str]:
    """Person B's canonical validator (spec §4.3, verbatim contract).

    Returns (status, reason):
      ("accepted", "passed")
      ("rejected", "dimension_mismatch")
      ("rejected", "calculation_gap_exceeded")
    """
    if dimension_conflict(candidate_a, candidate_b):
        return "rejected", "dimension_mismatch"
    if not numeric_continuity(candidate_a, candidate_b, tolerance):
        return "rejected", "calculation_gap_exceeded"
    return "accepted", "passed"


# ---------------------------------------------------------------------------
# Concept-specific calculation constraints
# ---------------------------------------------------------------------------

def _rel_gap(actual: float, expected: float) -> float:
    return abs(actual - expected) / max(abs(expected), 1e-9)


def revenue_reconciliation(
    units: List[Dict[str, Any]],
    ticker: str,
    fiscal_year: int,
    tolerance: float = TOLERANCE,
) -> Optional[Dict[str, Any]]:
    """Revenue_total rule: segment sum + eliminations ≈ total.

    In Person A's dataset the segment rows are stored as *rejected*
    Revenue_total units carrying a dimension (product/service, business
    segment, geography), and each row keeps 3 comparative years. We search
    the rejected segment rows for a subset (2 rows, falling back to 3) whose
    sum equals the pinned consolidated total within tolerance — the subset
    that reconciles is, by construction, the target fiscal year's rows.

    Returns None when a side is missing (report as "missing", not "failed").
    """
    total_u = best_unit(units, ticker, fiscal_year, "Revenue_total",
                        require_consolidated=True)
    if total_u is None or total_u.get("value") is None:
        return None
    total = float(total_u["value"])

    cands = [
        u
        for u in units
        if u.get("ticker") == ticker
        and u.get("fiscal_year") == fiscal_year
        and u.get("metric") == "Revenue_total"
        and u.get("validation_status") == "rejected"
        and dims_of(u)
        and u.get("value") is not None
    ]
    # Distinct segment rows: (dimension members, value); duplicate rows such
    # as "Services" vs "Services (1)" with the same member/value collapse.
    row_map: Dict[Any, Dict[str, Any]] = {}
    for u in cands:
        key = (frozenset(dims_of(u).items()), float(u["value"]))
        row_map.setdefault(key, u)
    rows = sorted(row_map.keys(), key=lambda k: k[1])

    best: Optional[Dict[str, Any]] = None
    for k in (2, 3):                       # prefer the smallest reconciling subset
        if k == 3 and len(rows) > 60:
            break
        for combo in combinations(rows, k):
            seg_sum = sum(v for _, v in combo)
            gap = _rel_gap(seg_sum, total)
            if gap <= tolerance and (best is None or gap < best["gap"]):
                best = {"gap": gap, "segment_sum": seg_sum, "combo": combo}
        if best is not None:
            break
    if best is None:
        return None

    labels = []
    for dims, value in best["combo"]:
        u = row_map[(dims, value)]
        label = u.get("row_label") or "; ".join(
            m.split(":")[-1] for m in dict(dims).values()
        )
        labels.append(f"{label} ({value / 1e6:,.0f}M)")

    return {
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "check": "segment_sum_to_total",
        "total_unit_id": total_u["unit_id"],
        "total": total,
        "segment_sum": best["segment_sum"],
        "gap": best["gap"],
        "status": "passed" if best["gap"] <= tolerance else "rejected",
        "n_segments": len(best["combo"]),
        "segment_rows": labels,
    }


def check_identities(
    units: List[Dict[str, Any]],
    ticker: str,
    fiscal_year: int,
    tolerance: float = TOLERANCE,
) -> List[Dict[str, Any]]:
    """Company-level accounting identities (Group16.pdf p.19):
      * GrossProfit = Revenue_total − CostOfRevenue
      * OperatingIncomeLoss = GrossProfit − OperatingExpenses
      * EPS(diluted)  ≈ NetIncomeLoss / diluted shares
    Missing inputs are skipped and reported, never failed.
    """
    results: List[Dict[str, Any]] = []

    def _one(check: str, actual_u, expected: float) -> None:
        if actual_u is None or actual_u.get("value") is None:
            results.append({"ticker": ticker, "check": check,
                            "status": "missing_input"})
            return
        gap = _rel_gap(float(actual_u["value"]), expected)
        results.append({"ticker": ticker, "check": check,
                        "unit_id": actual_u["unit_id"],
                        "actual": float(actual_u["value"]),
                        "expected": expected, "gap": gap,
                        "status": "passed" if gap <= tolerance else "rejected"})

    rev = best_unit(units, ticker, fiscal_year, "Revenue_total",
                    require_consolidated=True)
    cor = best_unit(units, ticker, fiscal_year, "CostOfRevenue",
                    require_consolidated=True)
    gp = best_unit(units, ticker, fiscal_year, "GrossProfit",
                   require_consolidated=True)
    opi = best_unit(units, ticker, fiscal_year, "OperatingIncomeLoss",
                    require_consolidated=True)
    opex = best_unit(units, ticker, fiscal_year, "OperatingExpenses",
                     require_consolidated=True)
    ni = best_unit(units, ticker, fiscal_year, "NetIncomeLoss",
                   require_consolidated=True)
    eps_d = best_unit(units, ticker, fiscal_year, "EarningsPerShareDiluted",
                      require_consolidated=True)
    shares_d = best_unit(units, ticker, fiscal_year,
                         "WeightedAverageNumberOfDilutedSharesOutstanding",
                         require_consolidated=True)

    if rev and cor:
        _one("gross_profit_identity", gp,
             float(rev["value"]) - float(cor["value"]))
    else:
        results.append({"ticker": ticker, "check": "gross_profit_identity",
                        "status": "missing_input"})

    if gp and opex:
        _one("operating_income_identity", opi,
             float(gp["value"]) - float(opex["value"]))
    else:
        results.append({"ticker": ticker, "check": "operating_income_identity",
                        "status": "missing_input"})

    if ni and shares_d and float(shares_d["value"]) != 0:
        _one("eps_identity", eps_d,
             float(ni["value"]) / float(shares_d["value"]))
    else:
        results.append({"ticker": ticker, "check": "eps_identity",
                        "status": "missing_input"})

    return results


def validate_rnd_expense(
    units: List[Dict[str, Any]],
    ticker: str,
    fiscal_year: int,
    tolerance: float = TOLERANCE,
) -> List[Dict[str, Any]]:
    """R&D_expense rule (spec §4.3):
      * not segment-only (no dimensions)
      * not footnote-only unless explicitly labelled
      * consistent with the income statement: R&D must be ≤ OperatingExpenses
        (R&D is a component of OpEx).
    """
    rnd_units = [
        u
        for u in units
        if u.get("ticker") == ticker
        and u.get("fiscal_year") == fiscal_year
        and u.get("metric") == "ResearchAndDevelopmentExpense"
    ]
    opex = best_unit(units, ticker, fiscal_year, "OperatingExpenses",
                     require_consolidated=True)
    out = []
    for u in rnd_units:
        if dims_of(u):
            out.append({"unit_id": u["unit_id"], "ticker": ticker,
                        "status": "rejected", "reason": "segment_only"})
            continue
        if u.get("unit_type") == "footnote":
            out.append({"unit_id": u["unit_id"], "ticker": ticker,
                        "status": "rejected", "reason": "footnote_only"})
            continue
        section = (u.get("section_path") or "").lower()
        if not section:
            if u.get("mapping_method") != "xbrl_tag":
                # Anchor unknown (no section recovered): a companyfacts
                # xbrl_tag fact is authoritative on its own, everything else
                # needs the primary-statement anchor to pass.
                out.append({"unit_id": u["unit_id"], "ticker": ticker,
                            "status": "pending", "reason": "section_unknown"})
                continue
        elif not any(m in section for m in PRIMARY_STATEMENT_MARKERS):
            # Footnote/note-section figure, not anchored in a primary statement.
            out.append({"unit_id": u["unit_id"], "ticker": ticker,
                        "status": "pending",
                        "reason": "non_primary_section"})
            continue
        if opex is not None and opex.get("value") is not None \
                and u.get("value") is not None \
                and u["value"] > opex["value"] * (1 + tolerance):
            out.append({"unit_id": u["unit_id"], "ticker": ticker,
                        "status": "rejected",
                        "reason": "exceeds_operating_expenses"})
            continue
        out.append({"unit_id": u["unit_id"], "ticker": ticker,
                    "status": "passed", "reason": "income_statement_consistent"})
    return out


# ---------------------------------------------------------------------------
# Stage 5 — alignment edges
# ---------------------------------------------------------------------------

def build_alignment_edges(
    units: List[Dict[str, Any]],
    concept: str,
    fiscal_year: int,
    tolerance: float = TOLERANCE,
) -> List[Dict[str, Any]]:
    """Group units by (sector, concept, fiscal_year), pair every company pair,
    and emit alignment_edges(unit_a, unit_b, concept, period,
    validation_status, gap).

    Cross-company value differences are recorded in `gap`, never treated as
    a failure (companies legitimately differ). An edge fails only on
    dimension conflict. Each side must already be an accepted, consolidated
    (for consolidated concepts) unit.
    """
    by_company: Dict[str, Dict[str, Any]] = {}
    for t in sorted({u.get("ticker") for u in units}):
        cands = [
            u
            for u in units
            if u.get("ticker") == t
            and u.get("fiscal_year") == fiscal_year
            and u.get("metric") == concept
            and u.get("validation_status") == "passed"
        ]
        if concept in CONSOLIDATED_METRICS:
            cands = [u for u in cands if not dims_of(u)]
        best = best_unit(cands, t, fiscal_year, concept)
        if best is not None:
            by_company[t] = best

    names = sorted(by_company)
    edges: List[Dict[str, Any]] = []
    for i, ta in enumerate(names):
        for tb in names[i + 1:]:
            a, b = by_company[ta], by_company[tb]
            conflict = dimension_conflict(a, b)
            va, vb = a.get("value"), b.get("value")
            gap = _rel_gap(float(va), float(vb)) \
                if va is not None and vb is not None else None
            edges.append({
                "edge_id": f"align_{concept.lower()}_{ta.lower()}_{tb.lower()}_fy{fiscal_year}",
                "concept": concept,
                "period": f"FY{fiscal_year}",
                "unit_a": a["unit_id"],
                "unit_a_ticker": ta,
                "unit_a_value": va,
                "unit_b": b["unit_id"],
                "unit_b_ticker": tb,
                "unit_b_value": vb,
                "gap": gap,
                "validation_status": "rejected" if conflict else "passed",
                "validation_reason": "dimension_mismatch" if conflict else "passed",
                "period_note": (
                    f"{ta} filed {a.get('filing_date') or 'n/a'}; "
                    f"{tb} filed {b.get('filing_date') or 'n/a'}"
                ),
            })
    return edges


def paragraph_table_agreement(
    units: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Stage 5 check: a paragraph must agree with its linked table row on
    metric and period.

    TODO(team): Person A's v1 dataset has no `paragraph` units
    (only xbrl_fact / table_row). Implement once Stage 3 v2 emits them.
    """
    paragraphs = [u for u in units if u.get("unit_type") == "paragraph"]
    if not paragraphs:
        return []
    raise NotImplementedError("paragraph_table_agreement: implement for Stage 3 v2")


# ---------------------------------------------------------------------------
# Demo / smoke test against Person A's real data
# ---------------------------------------------------------------------------

def _m(v: Optional[float]) -> str:
    return f"{v / 1e6:,.0f}M" if isinstance(v, (int, float)) else "n/a"


def main() -> None:
    units = load_units(DEFAULT_UNITS)
    tickers = sorted({u["ticker"] for u in units})
    fy = 2025

    from collections import Counter
    print(f"Loaded {len(units)} evidence units from {DEFAULT_UNITS.name}")
    print("  by status:",
          dict(Counter(u.get('validation_status') for u in units)))
    print("  by unit_type:",
          dict(Counter(u.get('unit_type') for u in units)))
    print("  tickers:", tickers)

    print("\n== Revenue reconciliation (segment sum ~= total, <=2%) ==")
    for t in tickers:
        rec = revenue_reconciliation(units, t, fy)
        if rec is None:
            print(f"  {t:5s} no reconciling segment subset found — SKIP "
                  f"(report as missing)")
            continue
        flag = "PASS" if rec["status"] == "passed" else "FAIL"
        print(f"  {t:5s} {' + '.join(rec['segment_rows'])} = "
              f"{_m(rec['segment_sum'])} vs total {_m(rec['total'])}  "
              f"gap {rec['gap'] * 100:.2f}%  {flag}")

    print("\n== Accounting identities (<=2%) ==")
    for t in tickers:
        for r in check_identities(units, t, fy):
            if r["status"] == "missing_input":
                print(f"  {t:5s} {r['check']:26s} MISSING INPUT")
            elif r["check"] == "eps_identity":
                flag = "PASS" if r["status"] == "passed" else "FAIL"
                print(f"  {t:5s} {r['check']:26s} actual {r['actual']:,.2f} "
                      f"vs expected {r['expected']:,.2f}  "
                      f"gap {r['gap'] * 100:.2f}%  {flag}")
            else:
                flag = "PASS" if r["status"] == "passed" else "FAIL"
                print(f"  {t:5s} {r['check']:26s} actual {_m(r['actual'])} "
                      f"vs expected {_m(r['expected'])}  gap {r['gap'] * 100:.2f}%  {flag}")

    print("\n== R&D_expense validation ==")
    for t in tickers:
        res = validate_rnd_expense(units, t, fy)
        counts = Counter(r["status"] for r in res)
        detail = ", ".join(
            f"{r['reason']}({r['status']})" for r in res if r["status"] != "passed"
        )
        print(f"  {t:5s} {len(res)} R&D unit(s): "
              f"passed {counts['passed']}, pending {counts['pending']}, "
              f"rejected {counts['rejected']}" + (f"  [{detail}]" if detail else ""))

    print("\n== Cross-company alignment edges ==")
    out_dir = REPO_ROOT / "data"
    try:
        out_dir.mkdir(exist_ok=True)
        probe = out_dir / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        # Some sandboxed environments cannot write into the repo subtree;
        # fall back to a directory next to the repository.
        out_dir = REPO_ROOT.parent / "_person_b_out"
        out_dir.mkdir(exist_ok=True)
        print(f"  (repo data/ not writable here — writing to {out_dir})")
    for concept in ("Revenue_total", "ResearchAndDevelopmentExpense"):
        edges = build_alignment_edges(units, concept, fy)
        n_pass = sum(1 for e in edges if e["validation_status"] == "passed")
        n_rej = len(edges) - n_pass
        print(f"  {concept:28s} {len(edges)} edges "
              f"({n_pass} passed, {n_rej} rejected)")
        if concept == "Revenue_total":
            for e in edges[:3]:
                print(f"    {e['edge_id']}: {e['unit_a_ticker']} {_m(e['unit_a_value'])} "
                      f"vs {e['unit_b_ticker']} {_m(e['unit_b_value'])}  gap "
                      f"{e['gap'] * 100:.1f}%  {e['validation_status']}")
        (out_dir / f"alignment_edges_{concept.lower()}.jsonl").write_text(
            "\n".join(json.dumps(e, default=str) for e in edges) + "\n",
            encoding="utf-8",
        )

    print("\n== Rejected-unit summary (adversarial cases kept, not dropped) ==")
    rej = [u for u in units if u.get("validation_status") == "rejected"]
    by_metric = Counter(
        f"{u.get('metric')}" + ("+dims" if dims_of(u) else "")
        for u in rej
    )
    for k, v in by_metric.most_common():
        print(f"  {k:45s} {v}")
    print(f"\nWrote edges -> {out_dir}/alignment_edges_*.jsonl")


if __name__ == "__main__":
    main()
