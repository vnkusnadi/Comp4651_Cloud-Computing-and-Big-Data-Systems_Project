"""
Person B — tests for Stage 4 deterministic mapping (src/mapping/deterministic.py).

Runs standalone against Person A's real evidence_units:
    python tests/test_deterministic_mapping.py
or under pytest (from the repository root):
    python -m pytest tests/test_deterministic_mapping.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.mapping.deterministic import (  # noqa: E402
    acceptance_check,
    run_mapper,
)

_BUNDLE = None


def _load():
    global _BUNDLE
    if _BUNDLE is None:
        _BUNDLE = run_mapper()
    return _BUNDLE


def test_revenue_total_is_mapped_passed_via_xbrl_tag():
    """AAPL and MSFT total revenue must be accepted via the tag tier."""
    b = _load()
    for ticker, value in (("AAPL", 416161000000), ("MSFT", 281724000000)):
        hits = [
            m for m in b["mappings"]
            if m["ticker"] == ticker and m["concept"] == "Revenue_total"
            and m["value"] == value
        ]
        assert hits, f"{ticker} Revenue_total {value:,} not mapped"
        assert any(
            m["validation_status"] == "passed"
            and m["mapping_method"] == "xbrl_tag"
            for m in hits
        ), f"{ticker}: expected passed xbrl_tag mapping, got {hits[0]}"


def test_segment_revenue_is_rejected_with_dimension_mismatch():
    """The adversarial segment rows must appear in rejected_mappings with
    reason dimension_mismatch."""
    b = _load()
    for ticker, value, label in (
        ("AAPL", 307003000000, "Products"),
        ("MSFT", 63946000000, "Product"),
        ("MSFT", 217778000000, "Service and other"),
    ):
        hits = [
            r for r in b["rejected"]
            if r["ticker"] == ticker and r["value"] == value
            and r["reason"] == "dimension_mismatch"
        ]
        assert hits, (f"{ticker} {label} {value:,}: not rejected with "
                      f"dimension_mismatch")


def test_segment_revenue_is_rerouted_to_segment_concepts():
    """The same rows must also map to Revenue_product / Revenue_service."""
    b = _load()
    for ticker, concept, value in (
        ("AAPL", "Revenue_product", 307003000000),
        ("AAPL", "Revenue_service", 109158000000),
        ("MSFT", "Revenue_product", 63946000000),
        ("MSFT", "Revenue_service", 217778000000),
    ):
        hits = [
            m for m in b["mappings"]
            if m["ticker"] == ticker and m["concept"] == concept
            and m["value"] == value and m["validation_status"] == "passed"
        ]
        assert hits, f"{ticker} {concept} {value:,}: not mapped as passed"


def test_rnd_expense_mapped_passed():
    b = _load()
    for ticker, value in (("AAPL", 34550000000), ("MSFT", 32488000000)):
        hits = [
            m for m in b["mappings"]
            if m["ticker"] == ticker and m["concept"] == "R&D_expense"
            and m["value"] == value and m["validation_status"] == "passed"
        ]
        assert hits, f"{ticker} R&D_expense {value:,} not mapped as passed"


def test_every_rejected_entry_carries_a_reason():
    b = _load()
    missing = [r for r in b["rejected"] if not r.get("reason")]
    assert not missing, f"{len(missing)} rejected entries without a reason"


def test_acceptance_against_verified_dictionary():
    """Every accepted entry of the AAPL/MSFT company dictionary must be
    reproduced by the mapper (spec week-4 deliverable)."""
    b = _load()
    fails, notes = acceptance_check(b)
    for n in notes:
        print(f"  NOTE  {n}")
    assert not fails, "dictionary acceptance failures: " + "; ".join(fails)


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
