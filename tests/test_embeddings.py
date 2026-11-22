"""
Person B — tests for the embedding fallback (src/mapping/embeddings.py).

Runs standalone against Person A's real evidence_units:
    python tests/test_embeddings.py
or under pytest (from the repository root):
    python -m pytest tests/test_embeddings.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.mapping.deterministic import run_mapper  # noqa: E402
from src.mapping.embeddings import (  # noqa: E402
    get_backend,
    propose,
    run_proposals,
    verify_candidate,
)

_BUNDLE = None
_RES = None


def _load():
    global _BUNDLE, _RES
    if _BUNDLE is None:
        _BUNDLE = run_mapper()
        _RES = run_proposals(bundle=_BUNDLE, backend=get_backend())
    return _BUNDLE, _RES


def test_backend_available_without_dependencies():
    backend = get_backend()
    assert backend.threshold > 0
    assert backend.similarity("revenue", "revenue") > 0.9


def test_derived_duplicate_units_are_skipped_not_proposed():
    _, res = _load()
    assert res["stats"]["skipped_derived_duplicates"] == 20, (
        f"expected 20 derived duplicates, got "
        f"{res['stats']['skipped_derived_duplicates']}"
    )


def test_proposals_are_never_auto_accepted():
    """An embedding proposal is accepted only with a deterministic
    verification reason — never on the strength of the embedding alone."""
    _, res = _load()
    allowed_reasons = {
        "value_confirmed_by_deterministic_mapping",
        "identity_confirmed",
        "income_statement_consistent",
    }
    for c in res["candidates"]:
        if c["validation_status"] == "passed":
            assert c["reason"] in allowed_reasons, (
                f"auto-accepted proposal without verification: {c}"
            )


def test_nvda_revenue_verified_by_value_confirmation():
    """The flagship case: NVDA's untagged 'Revenue' row (130,497M) is
    proposed for Revenue_total and confirmed by the deterministic tier's
    accepted value (130,497,000,000) — semantic proposal + deterministic
    verification."""
    _, res = _load()
    hits = [
        c for c in res["candidates"]
        if c["ticker"] == "NVDA" and c["concept"] == "Revenue_total"
        and c["validation_status"] == "passed"
    ]
    assert hits, "NVDA Revenue_total proposal not verified"
    assert hits[0]["reason"] == "value_confirmed_by_deterministic_mapping"


def test_iprd_row_gets_no_candidate():
    """AVGO's 'IPR&D' row (an intangible asset, not R&D expense) must not
    reach the proposal threshold — a correct negative."""
    _, res = _load()
    iprd = [c for c in res["candidates"]
            if str(c.get("row_label")) == "IPR&D"]
    assert not iprd, f"IPR&D should not be proposed, got {iprd}"


def test_dimension_gate_rejects_consolidated_proposal():
    """Synthetic case: a dimensioned unit whose label looks like the total
    must be rejected with dimension_mismatch even when the embedding
    proposes Revenue_total."""
    b, res = _load()
    backend = get_backend()
    synth = {
        "unit_id": "synth-1", "ticker": "AAPL", "fiscal_year": 2025,
        "row_label": "Total net sales",
        "concept": "html_table_row", "unit_type": "table_row",
        "value": 416161000000.0,
        "dimensions": {"srt:ProductOrServiceAxis": "us-gaap:ProductMember"},
    }
    props = propose(synth, b["schema"], backend)
    assert props, "synthetic unit should get a proposal"
    status, reason = verify_candidate({
        **synth, "concept": props[0][0], "confidence": props[0][1],
    }, b)
    assert (status, reason) == ("rejected", "dimension_mismatch"), (
        f"expected rejected/dimension_mismatch, got {status}/{reason}"
    )


def test_confidences_within_range():
    _, res = _load()
    for c in res["candidates"]:
        assert 0.0 <= c["confidence"] <= 1.0
        assert c["confidence"] >= res["stats"]["threshold"] - 1e-9


# ---------------------------------------------------------------------------
# Bug-1 regressions: the R&D <= OpEx gate must reject ONLY values above
# OpEx (+2 %), never values below it (the original code had the logic
# inverted), and footnote-only candidates are rejected.
# AAPL's accepted OpEx in the real data is 62,151M (=> limit 63,394M).
# ---------------------------------------------------------------------------

def _rnd_candidate(value, unit_type="table_row",
                   section="consolidated statements of operations"):
    return {
        "unit_id": "synth-rnd", "ticker": "AAPL", "fiscal_year": 2025,
        "concept": "R&D_expense", "confidence": 0.8,
        "mapping_method": "embedding", "value": value,
        "row_label": "Research and development",
        "section_path": section, "unit_type": unit_type, "dimensions": {},
    }


def test_rnd_below_opex_is_not_rejected():
    """20,000M < OpEx: must NOT be rejected (original bug rejected it)."""
    b, _ = _load()
    status, reason = verify_candidate(_rnd_candidate(20000000000.0), b)
    assert (status, reason) == ("passed", "income_statement_consistent"), (
        f"got {status}/{reason}"
    )


def test_rnd_above_opex_is_rejected():
    """63,400M > OpEx*1.02 (63,394M): must be rejected."""
    b, _ = _load()
    status, reason = verify_candidate(_rnd_candidate(63400000000.0), b)
    assert (status, reason) == ("rejected", "exceeds_operating_expenses"), (
        f"got {status}/{reason}"
    )


def test_footnote_only_rnd_is_rejected():
    b, _ = _load()
    status, reason = verify_candidate(
        _rnd_candidate(34550000000.0, unit_type="footnote"), b)
    assert (status, reason) == ("rejected", "footnote_only"), (
        f"got {status}/{reason}"
    )


def test_rnd_without_primary_statement_anchor_stays_pending():
    b, _ = _load()
    status, _reason = verify_candidate(
        _rnd_candidate(20000000000.0, section=""), b)
    assert status == "pending", f"got {status}"


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
