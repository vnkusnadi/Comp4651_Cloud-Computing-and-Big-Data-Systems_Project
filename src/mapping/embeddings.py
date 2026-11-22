"""
Person B — Stage 4b: embedding fallback for concept mapping.

Per the project spec, semantic matching is the LAST tier and is
**candidate-only**: embeddings (or an LLM) may PROPOSE a mapping, but a
calculation-constraint / numeric-continuity check must VERIFY it before it
is accepted. This module never accepts a proposal on its own.

Backend:
  * sentence-transformers (primary, when installed) — threshold from
    configs/pipeline.yaml `mapping.min_embedding_score` (0.75)
  * dependency-free character-3-gram cosine fallback so the pipeline runs
    and is testable without model downloads (threshold 0.5)

Verification of each proposal (all deterministic, no LLM):
  1. dimension gate      — consolidated-only concepts reject dimensioned units
  2. value confirmation  — same (ticker, concept, value) already accepted by
                           the deterministic tier => confirmed (scale-tolerant)
  3. identity checks     — GrossProfit = Revenue - CostOfRevenue,
                           OperatingIncome = GrossProfit - OpEx,
                           EPS = NetIncome / shares
  4. R&D rule            — <= OperatingExpenses AND anchored in a primary
                           income statement (not balance sheet / footnotes)
  else                   — stays `pending` (candidate awaiting context check)

Output: data/embedding_candidates.jsonl (proposals + verification result).

Demo:
    python src/mapping/embeddings.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.align.validate import PRIMARY_STATEMENT_MARKERS
from src.mapping.deterministic import (  # noqa: E402
    bare,
    loose,
    run_mapper,
)
from src.util.yaml import load as yaml_load

REPO_ROOT = _REPO_ROOT
SCHEMA_PATH = REPO_ROOT / "configs" / "sectors" / "information_technology.yaml"
PIPELINE_PATH = REPO_ROOT / "configs" / "pipeline.yaml"
UNITS_PATH = (
    REPO_ROOT / "data_ingestion_and_processing" / "data" / "evidence_units"
    / "evidence_units.jsonl"
)
OUT_DIR = REPO_ROOT / "data"

TOLERANCE = 0.02
_SCALES = (1.0, 1e3, 1e6, 1e9)     # HTML-table rows may be in thousands/millions

INCOME_MARKERS = tuple(
    m for m in PRIMARY_STATEMENT_MARKERS
    if "operation" in m or "income" in m or "earning" in m
)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class CharNGramEmbedder:
    """Dependency-free fallback: cosine similarity over character 3-grams."""

    backend = "char_ngram"
    threshold = 0.5

    def __init__(self, n: int = 3):
        self.n = n

    def embed(self, text: str) -> Dict[str, int]:
        s = "#" + (text or "").lower() + "#"
        return Counter(
            s[i:i + self.n] for i in range(max(0, len(s) - self.n + 1))
        )

    def containment(self, text_a: str, text_b: str) -> float:
        """Fraction of `text_a`'s 3-grams present in `text_b`.

        Length-insensitive on the concept side, which suits the short
        label-vs-long dictionary-text setting (a query "Revenue" is fully
        explained by a dictionary that contains "revenue")."""
        ga, gb = self.embed(text_a), self.embed(text_b)
        if not ga:
            return 0.0
        return sum(1 for g in ga if g in gb) / len(ga)

    def similarity(self, text_a: str, text_b: str) -> float:
        va, vb = self.embed(text_a), self.embed(text_b)
        if not va or not vb:
            return 0.0
        dot = sum(va[g] * vb.get(g, 0) for g in va)
        na = math.sqrt(sum(x * x for x in va.values()))
        nb = math.sqrt(sum(x * x for x in vb.values()))
        return dot / (na * nb) if na and nb else 0.0


class _SBertBackend:
    """Primary backend: sentence-transformers embeddings + cosine."""

    backend = "sentence_transformers"

    def __init__(self, model, threshold: float):
        self.model = model
        self.threshold = threshold

    def embed(self, text: str):
        return self.model.encode(text)

    def similarity(self, text_a: str, text_b: str) -> float:
        import numpy as np
        a = np.asarray(self.embed(text_a), dtype=float)
        b = np.asarray(self.embed(text_b), dtype=float)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(a @ b / denom) if denom else 0.0


def get_backend():
    """Sentence-transformers when installed, else the char-ngram fallback."""
    cfg = yaml_load(PIPELINE_PATH) or {}
    min_score = (cfg.get("mapping") or {}).get("min_embedding_score", 0.75)
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        print("backend: sentence-transformers (all-MiniLM-L6-v2)")
        return _SBertBackend(SentenceTransformer("all-MiniLM-L6-v2"),
                             min_score)
    except ImportError:
        print("backend: char_ngram fallback (sentence-transformers not "
              f"installed; threshold {CharNGramEmbedder.threshold})")
        return CharNGramEmbedder()


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------

def build_concept_texts(schema: Dict[str, Any]) -> Dict[str, str]:
    """One searchable text per base concept: canonical name + aliases +
    description (all normalized)."""
    texts: Dict[str, str] = {}
    for c in schema.get("base_concepts") or []:
        parts = [c.get("canonical_name") or "", c.get("description") or ""]
        parts += list(c.get("aliases") or [])
        texts[c["canonical_name"]] = loose(" ".join(parts))
    return texts


def unit_text(u: Dict[str, Any]) -> str:
    """Signature of a unit for semantic search.

    The row label carries the signal; section words ("consolidated
    statements of ...") appear in no concept text and would only dilute
    the score. The `html_table_row` pseudo-concept adds no signal."""
    concept = str(u.get("concept") or "")
    parts = [u.get("row_label") or ""]
    if concept and not concept.startswith("derived:") \
            and concept != "html_table_row":
        parts.append(bare(concept))
    return loose(" ".join(parts))


def propose(
    u: Dict[str, Any],
    schema: Dict[str, Any],
    backend,
    k: int = 3,
) -> List[Tuple[str, float]]:
    """Return up to `k` (concept, score) candidates at/above the backend
    threshold. Empty when the unit text carries no signal.

    Scoring: containment (fraction of the unit's n-grams explained by the
    concept text) is the primary score for the n-gram backend, with cosine
    as tiebreaker; the sentence-transformers backend uses plain cosine."""
    ut = unit_text(u)
    if not ut.strip():
        return []
    concept_texts = build_concept_texts(schema)
    if hasattr(backend, "containment"):
        scored = sorted(
            ((name, backend.containment(ut, text),
              backend.similarity(ut, text))
             for name, text in concept_texts.items()),
            key=lambda x: (-x[1], -x[2]),
        )
        return [(n, round(c, 4)) for n, c, _s in scored[:k]
                if c >= backend.threshold]
    scored = sorted(
        ((name, backend.similarity(ut, text))
         for name, text in concept_texts.items()),
        key=lambda x: -x[1],
    )
    return [(n, round(s, 4)) for n, s in scored[:k] if s >= backend.threshold]


# ---------------------------------------------------------------------------
# Verification (deterministic gate — the LLM/embedding never decides alone)
# ---------------------------------------------------------------------------

def _accepted_values(bundle: Dict[str, Any]) -> Dict[Tuple[str, str], List[float]]:
    out: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for m in bundle["mappings"]:
        if m["validation_status"] == "passed" and m.get("value") is not None:
            out[(m["ticker"], m["concept"])].append(float(m["value"]))
    return out


def _match_scaled(value: float, refs: List[float], tol: float = TOLERANCE) -> bool:
    """Scale-tolerant comparison: HTML-table rows may be in millions."""
    for ref in refs:
        for s in _SCALES:
            if abs(value * s - ref) <= tol * max(abs(ref), 1e-9):
                return True
    return False


def _first(vals: List[float]) -> Optional[float]:
    return vals[0] if vals else None


def _identity_expected(
    concept: str, ticker: str, accepted: Dict[Tuple[str, str], List[float]]
) -> Optional[float]:
    if concept == "GrossProfit":
        rev, cor = _first(accepted.get((ticker, "Revenue_total"), [])), \
            _first(accepted.get((ticker, "CostOfRevenue"), []))
        return rev - cor if rev is not None and cor is not None else None
    if concept == "OperatingIncomeLoss":
        gp = _first(accepted.get((ticker, "GrossProfit"), []))
        opex = _first(accepted.get((ticker, "OperatingExpenses"), []))
        return gp - opex if gp is not None and opex is not None else None
    if concept in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
        ni = _first(accepted.get((ticker, "NetIncomeLoss"), []))
        sh_metric = ("WeightedAverageNumberOfDilutedSharesOutstanding"
                     if concept.endswith("Diluted")
                     else "WeightedAverageNumberOfSharesOutstandingBasic")
        sh = _first(accepted.get((ticker, sh_metric), []))
        return ni / sh if ni is not None and sh else None
    return None


def verify_candidate(
    candidate: Dict[str, Any],
    bundle: Dict[str, Any],
    tolerance: float = TOLERANCE,
) -> Tuple[str, str]:
    """Deterministic verification of an embedding proposal.

    Returns (validation_status, reason). The proposal is NEVER accepted on
    the strength of the embedding alone."""
    ticker = candidate["ticker"]
    concept = candidate["concept"]
    value = candidate.get("value")
    schema = bundle["schema"]
    base = next((c for c in schema.get("base_concepts") or []
                 if c.get("canonical_name") == concept), None)
    if base is None:
        return "rejected", "unknown_concept"

    dims = candidate.get("dimensions") or {}
    if (base.get("dimensions") in (None, "", "none") and dims):
        return "rejected", "dimension_mismatch"

    # R&D-specific, checked BEFORE any confirmation: a footnote-only figure
    # is never accepted as R&D, even if its value coincides with an
    # accepted unit (spec §4.3: "not a segment-only or footnote-only
    # figure unless explicitly labeled").
    if concept == "R&D_expense" and candidate.get("unit_type") == "footnote":
        return "rejected", "footnote_only"

    accepted = _accepted_values(bundle)

    # 1. value already accepted for this (ticker, concept) => confirmed
    if value is not None and _match_scaled(
            float(value), accepted.get((ticker, concept), []), tolerance):
        return "passed", "value_confirmed_by_deterministic_mapping"

    # 2. accounting identity (exact arithmetic, scale-tolerant)
    expected = _identity_expected(concept, ticker, accepted)
    if expected is not None and value is not None and _match_scaled(
            float(value), [expected], tolerance):
        return "passed", "identity_confirmed"

    # 3. R&D rule: <= OpEx; anchored in a primary income statement (not
    # balance sheet / footnotes). (Footnote-only was already rejected above.)
    if concept == "R&D_expense":
        opex = _first(accepted.get((ticker, "OperatingExpenses"), []))
        section = (candidate.get("section_path") or "").lower()
        in_income_statement = any(m in section for m in INCOME_MARKERS)
        if value is not None and opex is not None:
            # R&D is a component of OpEx: ONLY a value above OpEx (+2%) is a
            # violation; anything <= OpEx passes the constraint. Scale the
            # candidate to the magnitude nearest OpEx before comparing
            # (HTML-table rows may be in millions).
            scaled = [float(value) * s for s in _SCALES]
            closest = min(scaled, key=lambda v: abs(v - opex))
            if closest > opex * (1 + tolerance):
                return "rejected", "exceeds_operating_expenses"
            if in_income_statement:
                return "passed", "income_statement_consistent"
            return "pending", "candidate_awaiting_context_check"
        return "pending", "candidate_awaiting_context_check"

    return "pending", "candidate_awaiting_context_check"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_proposals(
    bundle: Optional[Dict[str, Any]] = None,
    backend=None,
) -> Dict[str, Any]:
    """Propose concept candidates for the units the deterministic tier
    could not map, then verify every proposal through the deterministic
    gate. Returns candidates + stats."""
    if bundle is None:
        bundle = run_mapper()
    if backend is None:
        backend = get_backend()

    unmapped = bundle["unmapped"]
    skipped_derived = [
        u for u in unmapped if str(u.get("concept") or "").startswith("derived:")
    ]
    units = [
        u for u in unmapped if not str(u.get("concept") or "").startswith("derived:")
    ]

    candidates: List[Dict[str, Any]] = []
    for u in units:
        for concept, score in propose(u, bundle["schema"], backend):
            cand = {
                "unit_id": u.get("unit_id"),
                "ticker": u.get("ticker"),
                "fiscal_year": u.get("fiscal_year"),
                "concept": concept,
                "confidence": score,
                "mapping_method": "embedding",   # spec Stage 4 vocabulary
                "validation_status": "pending",
                "reason": None,
                "value": u.get("value"),
                "row_label": u.get("row_label"),
                "section_path": u.get("section_path"),
                "unit_type": u.get("unit_type"),
                "dimensions": u.get("dimensions") or {},
            }
            status, reason = verify_candidate(cand, bundle)
            cand["validation_status"], cand["reason"] = status, reason
            candidates.append(cand)

    statuses = Counter(c["validation_status"] for c in candidates)
    stats = {
        "backend": backend.backend,
        "threshold": backend.threshold,
        "unmapped_units": len(units),
        "skipped_derived_duplicates": len(skipped_derived),
        "units_with_candidates": len({c["unit_id"] for c in candidates}),
        "candidates": len(candidates),
        "by_status": dict(statuses),
    }
    return {"candidates": candidates, "stats": stats,
            "skipped_derived": skipped_derived}


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _out_dir() -> Path:
    out = OUT_DIR
    try:
        out.mkdir(exist_ok=True)
        probe = out / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        out = REPO_ROOT.parent / "_person_b_out"
        out.mkdir(exist_ok=True)
        print(f"(repo data/ not writable here - writing to {out})")
    return out


def main() -> None:
    res = run_proposals()
    stats = res["stats"]
    print(f"backend={stats['backend']} threshold={stats['threshold']}")
    print(f"{stats['unmapped_units']} semantic candidates units + "
          f"{stats['skipped_derived_duplicates']} derived duplicates skipped")
    print(f"-> {stats['candidates']} proposals "
          f"({stats['units_with_candidates']} units), statuses "
          f"{stats['by_status']}")

    print("\n== Proposals (concept | score | verification) ==")
    for c in res["candidates"]:
        print(f"  {c['ticker']:5} {c['concept']:<40} conf={c['confidence']:.3f} "
              f"-> {c['validation_status']:<8} ({c['reason']}) "
              f"row={c['row_label']!r} value={c['value']}")

    if res["skipped_derived"]:
        print("\n== Skipped derived-duplicate units (kept out by design) ==")
        for u in res["skipped_derived"][:6]:
            print(f"  {u['ticker']:5} concept={u.get('concept')!r} "
                  f"value={u.get('value')}")
        if len(res["skipped_derived"]) > 6:
            print(f"  ... {len(res['skipped_derived']) - 6} more")

    out = _out_dir()
    (out / "embedding_candidates.jsonl").write_text(
        "\n".join(json.dumps(c, default=str) for c in res["candidates"]) + "\n",
        encoding="utf-8")
    print(f"\nWrote embedding_candidates -> {out}")


if __name__ == "__main__":
    main()
