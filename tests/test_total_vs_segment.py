"""
Person B-owned test (course spec §5, "Four-person division"):

    Own test_total_vs_segment.py: assert that segment revenue is rejected
    for Revenue_total and total revenue is selected.

Runs standalone against Person A's real evidence_units:
    python tests/test_total_vs_segment.py
or under pytest (from the repository root):
    python -m pytest tests/test_total_vs_segment.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.align.validate import (  # noqa: E402
    build_alignment_edges,
    dims_of,
    load_units,
    revenue_reconciliation,
)

DATA = (
    ROOT
    / "data_ingestion_and_processing"
    / "data"
    / "evidence_units"
    / "evidence_units.jsonl"
)
TICKERS = ["AAPL", "MSFT", "NVDA", "AVGO", "CSCO"]
FY = 2025

_UNITS = None


def _load():
    global _UNITS
    if _UNITS is None:
        _UNITS = load_units(DATA)
    return _UNITS


def test_total_revenue_is_selected_and_passed():
    """Every company must have exactly one accepted consolidated
    Revenue_total (the total is selected, not the segment rows)."""
    units = _load()
    for t in TICKERS:
        passed = [
            u for u in units
            if u.get("ticker") == t
            and u.get("fiscal_year") == FY
            and u.get("metric") == "Revenue_total"
            and u.get("validation_status") == "passed"
            and u.get("mapping_method") == "xbrl_tag"
            and not dims_of(u)
        ]
        assert len(passed) == 1, (
            f"{t}: expected exactly 1 passed consolidated Revenue_total, "
            f"got {len(passed)}"
        )


def test_segment_revenue_is_rejected_for_revenue_total():
    """Every company must have ≥1 rejected revenue candidate carrying a
    segment dimension (the adversarial case must be exercised)."""
    units = _load()
    for t in TICKERS:
        rejected = [
            u for u in units
            if u.get("ticker") == t
            and u.get("fiscal_year") == FY
            and u.get("validation_status") == "rejected"
            and u.get("metric") in ("Revenue_total", "Revenue_product",
                                    "Revenue_service")
            and dims_of(u)
        ]
        assert rejected, (
            f"{t}: zero rejected segment-revenue candidates — the "
            f"total-vs-segment test case is not exercised"
        )


def test_aapl_products_row_is_the_rejected_adversarial_case():
    """AAPL's Products row ($307,003M) must be rejected as Revenue_total —
    it is one product line, not the company total."""
    units = _load()
    products = [
        u for u in units
        if u.get("ticker") == "AAPL"
        and u.get("fiscal_year") == FY
        and u.get("value") == 307003000000.0
        and "Product" in (u.get("row_label") or "")
    ]
    assert products, "AAPL Products row ($307,003M) not found"
    assert any(u.get("validation_status") == "rejected" for u in products), (
        f"AAPL Products row must be rejected, got statuses "
        f"{[u.get('validation_status') for u in products]}"
    )


def test_segment_sum_reconciles_to_total_within_tolerance():
    """Revenue_total rule: segment sum + eliminations ≈ total (≤2%)."""
    units = _load()
    for t in TICKERS:
        rec = revenue_reconciliation(units, t, FY)
        assert rec is not None, (
            f"{t}: missing total or segment units — cannot reconcile"
        )
        assert rec["status"] == "passed", (
            f"{t}: segment sum {rec['segment_sum']:,.0f} vs total "
            f"{rec['total']:,.0f} gap {rec['gap'] * 100:.2f}% exceeds 2%"
        )


def test_revenue_total_edges_use_only_accepted_consolidated_units():
    """Cross-company Revenue_total edges must connect only accepted,
    dimension-free totals (5 companies → C(5,2) = 10 edges)."""
    units = _load()
    edges = build_alignment_edges(units, "Revenue_total", FY)
    assert len(edges) == 10, f"expected 10 edges, got {len(edges)}"
    for e in edges:
        assert e["validation_status"] == "passed", (
            f"{e['edge_id']}: {e['validation_reason']}"
        )
        # Both endpoints must be consolidated (no segment dimension).
        for u in _load():
            if u["unit_id"] in (e["unit_a"], e["unit_b"]):
                assert not dims_of(u), (
                    f"{e['edge_id']}: endpoint {u['unit_id']} carries a "
                    f"segment dimension"
                )


def _run_all() -> int:
    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
