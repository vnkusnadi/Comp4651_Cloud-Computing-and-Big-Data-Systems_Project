"""
Person B — R&D intensity tests (spec: the R&D-intensity question requires
values consistent with the income statement, never segment-only or
footnote-only).

Covers:
  * R&D_expense mappings are accepted via the tag tier for AAPL/MSFT
  * segment-only R&D rows are rejected (dimension_mismatch)
  * the R&D rules hold (no footnote-only acceptance, R&D <= OpEx)
  * R&D intensity = R&D_expense / Revenue_total, recomputed from mapped
    values, matches Person A's derived units and the dictionary
  * the cross-company R&D alignment edges exist and pass

Runs standalone against Person A's real evidence_units:
    python tests/test_rnd_intensity.py
or under pytest (from the repository root):
    python -m pytest tests/test_rnd_intensity.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.align.validate import (  # noqa: E402
    build_alignment_edges,
    load_units,
    validate_rnd_expense,
)
from src.mapping.deterministic import run_mapper  # noqa: E402

UNITS_PATH = (
    ROOT / "data_ingestion_and_processing" / "data" / "evidence_units"
    / "evidence_units.jsonl"
)
TOLERANCE = 0.02

# Verified values from the company dictionary (configs/sectors/
# information_technology.yaml, validated against the original 10-Ks).
GOLD = {
    "AAPL": {"R&D_expense": 34550000000, "Revenue_total": 416161000000,
             "intensity": 34550000000 / 416161000000},
    "MSFT": {"R&D_expense": 32488000000, "Revenue_total": 281724000000,
             "intensity": 32488000000 / 281724000000},
}

_BUNDLE = None
_UNITS = None


def _load():
    global _BUNDLE, _UNITS
    if _BUNDLE is None:
        _BUNDLE = run_mapper()
        _UNITS = load_units(UNITS_PATH)
    return _BUNDLE, _UNITS


def _passed_value(bundle, ticker, concept):
    hits = [
        m["value"] for m in bundle["mappings"]
        if m["ticker"] == ticker and m["concept"] == concept
        and m["validation_status"] == "passed" and m["value"] is not None
    ]
    assert hits, f"{ticker} {concept}: no passed mapping"
    # prefer the tag-tier unit; all passed values must agree within tolerance
    ref = hits[0]
    for v in hits[1:]:
        assert abs(v - ref) <= TOLERANCE * max(abs(ref), 1e-9), (
            f"{ticker} {concept}: conflicting passed values {hits}"
        )
    return ref


def _intensity(bundle, ticker):
    return (_passed_value(bundle, ticker, "R&D_expense")
            / _passed_value(bundle, ticker, "Revenue_total"))


def test_rnd_expense_mapped_to_gold_values():
    """R&D mappings must reproduce the dictionary's verified values."""
    b, _ = _load()
    for ticker, gold in GOLD.items():
        got = _passed_value(b, ticker, "R&D_expense")
        assert abs(got - gold["R&D_expense"]) <= TOLERANCE * gold["R&D_expense"], (
            f"{ticker}: R&D {got:,} != gold {gold['R&D_expense']:,}"
        )


def test_segment_only_rnd_is_rejected():
    """AAPL and AVGO's segment-note R&D rows (dimensioned) must land in
    rejected_mappings with dimension_mismatch — never accepted as the
    consolidated R&D_expense."""
    b, _ = _load()
    for ticker in ("AAPL", "AVGO"):
        hits = [
            r for r in b["rejected"]
            if r["ticker"] == ticker and r["concept"] == "R&D_expense"
            and r["reason"] == "dimension_mismatch"
        ]
        assert hits, f"{ticker}: no rejected segment R&D candidate"


def test_rnd_validation_rules_hold():
    """validate_rnd_expense: passed R&D units must be consolidated,
    non-footnote, and consistent with the income statement (<= OpEx);
    units without a recovered primary-statement anchor stay pending."""
    _, units = _load()
    for ticker in ("AAPL", "MSFT", "NVDA", "AVGO", "CSCO"):
        results = validate_rnd_expense(units, ticker, 2025)
        passed = [r for r in results if r["status"] == "passed"]
        rejected = [r for r in results if r["status"] == "rejected"]
        assert passed, f"{ticker}: no passed R&D unit"
        for r in passed:
            assert r["reason"] == "income_statement_consistent"
        for r in rejected:
            assert r["reason"] in ("segment_only", "footnote_only",
                                   "exceeds_operating_expenses")
    # regression (Bug 2a): a section-less iXBRL R&D unit whose anchor cannot
    # be confirmed must stay pending, not pass
    aapl = [r for r in validate_rnd_expense(units, "AAPL", 2025)
            if r["unit_id"] == "e38a9380d340cbf4"]
    assert aapl, "unit e38a9380d340cbf4 not found in R&D validation"
    assert aapl[0]["status"] == "pending", (
        f"e38a9380d340cbf4 must be pending (anchor unknown), got {aapl[0]}"
    )


def test_rnd_intensity_matches_person_a_derived_units():
    """Recompute R&D/Revenue from mapped base values; must equal Person A's
    `calculation` unit for R&D_expense_to_revenue (arithmetic is never
    inferred)."""
    b, _ = _load()
    for ticker in GOLD:
        calc = [m for m in b["mappings"]
                if m["ticker"] == ticker
                and m["concept"] == "R&D_expense_to_revenue"
                and m["mapping_method"] == "calculation"]
        assert calc, f"{ticker}: missing derived R&D_expense_to_revenue"
        assert abs(_intensity(b, ticker) - calc[0]["value"]) <= 1e-9, (
            f"{ticker}: recomputed intensity diverges from Person A's "
            f"derived unit"
        )


def test_rnd_intensity_matches_dictionary():
    b, _ = _load()
    for ticker, gold in GOLD.items():
        got = _intensity(b, ticker)
        assert abs(got - gold["intensity"]) <= TOLERANCE * gold["intensity"], (
            f"{ticker}: intensity {got:.4%} != gold {gold['intensity']:.4%}"
        )


def test_rnd_intensity_ranking_is_msft_over_aapl():
    """The spec's example question: MSFT's R&D intensity (11.5%) is larger
    than AAPL's (8.3%)."""
    b, _ = _load()
    aapl, msft = _intensity(b, "AAPL"), _intensity(b, "MSFT")
    assert msft > aapl, (
        f"expected MSFT ({msft:.2%}) > AAPL ({aapl:.2%})"
    )


def test_rnd_alignment_edges_exist_and_pass():
    """Cross-company R&D edges: C(5,2)=10 edges, all validated."""
    _, units = _load()
    edges = build_alignment_edges(units, "ResearchAndDevelopmentExpense", 2025)
    assert len(edges) == 10, f"expected 10 R&D edges, got {len(edges)}"
    assert all(e["validation_status"] == "passed" for e in edges)


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
