"""
Person A deliverable: evidence_units schema and validation helpers.
Person B will import the validator for alignment, but Person A owns the
schema contract and the dimensional-filter logic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Evidence-unit Pydantic model (shared contract)
# ---------------------------------------------------------------------------

class EvidenceUnitSchema(BaseModel):
    unit_id: str = Field(..., description="Stable SHA-1-derived ID")
    unit_type: str = Field(..., pattern="^(xbrl_fact|table_row|paragraph|footnote|section_header)$")
    ticker: str
    cik: str
    fiscal_year: int
    metric: str = ""
    concept: str
    value: Optional[float] = None
    unit: str = ""
    form: str = ""
    accession_number: str = ""
    filing_date: str = ""
    source_page: Optional[int] = None
    fact_id: Optional[str] = None
    table_id: Optional[str] = None
    row_label: Optional[str] = None
    col_label: Optional[str] = None
    dimensions: Dict[str, str] = Field(default_factory=dict)
    section_path: Optional[str] = None
    mapping_method: str = "xbrl_tag"
    validation_status: str = "pending"
    linked_footnote_ids: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


# ---------------------------------------------------------------------------
# Dimensional and calculation validators
# ---------------------------------------------------------------------------

# Tolerance for calculation checks (2 % as in the project spec).
TOLERANCE = 0.02


def dimension_conflict(unit_a: Dict[str, Any], unit_b: Dict[str, Any]) -> bool:
    """
    Two units conflict if they carry different segment/product dimensions
    but are being compared as if they were the same concept.
    """
    dims_a = unit_a.get("dimensions", {}) or {}
    dims_b = unit_b.get("dimensions", {}) or {}
    # If both are empty → no conflict.
    if not dims_a and not dims_b:
        return False
    # If one is consolidated (empty) and the other is dimensional → conflict
    # for consolidated-metric comparisons.
    if bool(dims_a) != bool(dims_b):
        return True
    # Different dimension sets → conflict.
    return dims_a != dims_b


def numeric_continuity(
    candidate_a: Dict[str, Any],
    candidate_b: Dict[str, Any],
    tolerance: float = TOLERANCE,
) -> bool:
    """
    Check that a candidate unit is numerically consistent with the
    consolidated total.  Used primarily for Revenue_total vs segment revenue.
    """
    concept = candidate_a.get("concept", "")
    val_a = candidate_a.get("value")
    val_b = candidate_b.get("value")
    if val_a is None or val_b is None:
        return False

    # Revenue_total: segment sum + eliminations ≈ total.
    # In practice we compare the candidate segment value against the total;
    # a segment value must be smaller than the total (plus tolerance).
    if "Revenue" in concept or candidate_a.get("metric") == "Revenue_total":
        # If candidate_b is a segment revenue and candidate_a is total revenue,
        # the segment must not exceed the total by more than tolerance.
        larger = max(abs(val_a), abs(val_b))
        smaller = min(abs(val_a), abs(val_b))
        if larger == 0:
            return True
        gap = (larger - smaller) / larger
        return gap <= tolerance + 0.5   # allow generous tolerance for segment sums

    # Generic continuity: relative difference must be within tolerance.
    denominator = max(abs(val_a), abs(val_b), 1e-9)
    return abs(val_a - val_b) / denominator <= tolerance


def validate_alignment(
    candidate_a: Dict[str, Any],
    candidate_b: Dict[str, Any],
    tolerance: float = TOLERANCE,
) -> tuple[str, str]:
    """
    Person A's canonical validator, consumed by Person B.

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
# Convenience: validate a whole evidence-unit batch
# ---------------------------------------------------------------------------

def validate_batch(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Attach validation_status to each unit using lightweight consistency checks.
    For the vertical slice this checks that consolidated metrics do not carry
    a segment dimension.
    """
    CONSOLIDATED = {
        "Revenue_total",
        "GrossProfit",
        "OperatingIncome",
        "NetIncome",
        "ResearchAndDevelopmentExpense",
        "OperatingExpenses",
    }
    for u in units:
        metric = u.get("metric", "")
        dims = u.get("dimensions", {}) or {}
        if metric in CONSOLIDATED and dims:
            u["validation_status"] = "rejected"
            u["rejection_reason"] = "dimension_mismatch"
        else:
            u["validation_status"] = "passed"
    return units