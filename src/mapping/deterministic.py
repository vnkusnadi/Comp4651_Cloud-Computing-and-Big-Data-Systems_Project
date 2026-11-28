"""
Person B — Stage 4: deterministic mapping of evidence units to sector concepts.

Maps every evidence unit to a schema concept using three tiers, in
reliability order (spec §4.2 Stage 4):

    1. xbrl_tag         — the unit's XBRL concept hits the schema's xbrl_tags
    2. exact_label      — the unit's row label exactly matches a schema alias
    3. normalized_label — a normalized (punctuation-stripped) label match

Rules:
  * Dimension gate first: a consolidated-only concept (dimensions: none)
    rejects any unit carrying a dimension — recorded in rejected_mappings
    with reason `dimension_mismatch` (never dropped silently).
  * Segment concepts (Revenue_product / Revenue_service) are selected by
    dimension axis + member when the shared revenue tag matches.
  * Label-only matches stay `pending` (row-name-only rule, Group16 p.8)
    until the math/context checks promote them.
  * `calculation` units are passed through against derived_metrics.

Output (paths from configs/pipeline.yaml):
    data/mappings.jsonl           unit -> concept
    data/rejected_mappings.jsonl  rejected candidate mappings + reason

Demo + acceptance vs. the AAPL/MSFT company dictionary:
    python src/mapping/deterministic.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:          # allow `python src/mapping/deterministic.py`
    sys.path.insert(0, str(_REPO_ROOT))

from src.align.validate import load_units
from src.util.yaml import load as yaml_load

REPO_ROOT = _REPO_ROOT
SCHEMA_PATH = REPO_ROOT / "configs" / "sectors" / "information_technology.yaml"
UNITS_PATH = (
    REPO_ROOT
    / "data_ingestion_and_processing"
    / "data"
    / "evidence_units"
    / "evidence_units.jsonl"
)
OUT_DIR = REPO_ROOT / "data"

# Unify typographic apostrophes before label matching (the 10-Ks use U+2019).
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u02bc": "'", "\u2018": "'"})


def bare(tag: str) -> str:
    return (tag or "").split(":")[-1]


def norm(s: str) -> str:
    s = (s or "").translate(_APOSTROPHES)
    return re.sub(r"\s+", " ", s).strip().lower()


def loose(s: str) -> str:
    s = norm(s)
    s = re.sub(r"\([^)]*\)", " ", s)          # footnote refs like "(1)"
    s = re.sub(r"[^a-z0-9&\s]", " ", s)       # strip punctuation
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Schema index
# ---------------------------------------------------------------------------

class SchemaIndex:
    """Fast lookup structures over the YAML schema's base concepts."""

    def __init__(self, schema: Dict[str, Any]):
        self.base: List[Dict[str, Any]] = schema.get("base_concepts") or []
        self.derived_names = {
            d.get("metric") for d in (schema.get("derived_metrics") or [])
        }
        self.tag_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.exact_alias: Dict[str, str] = {}
        self.loose_alias: Dict[str, str] = {}
        for c in self.base:
            for t in c.get("xbrl_tags") or []:
                self.tag_index[bare(t)].append(c)
            for a in c.get("aliases") or []:
                self.exact_alias.setdefault(norm(a), c["canonical_name"])
                self.loose_alias.setdefault(loose(a), c["canonical_name"])

    def base_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        for c in self.base:
            if c.get("canonical_name") == name:
                return c
        return None

    @staticmethod
    def dims_ok(concept: Dict[str, Any], dims: Dict[str, str]) -> bool:
        """Does the unit's dimension set satisfy the concept's dimension
        requirement? `none`/empty = consolidated only; an axis name means the
        unit must carry that axis (strict — used for tag-tier matches)."""
        req = concept.get("dimensions")
        if not req or req == "none":
            return not dims
        return any(req in k for k in dims)

    @staticmethod
    def dims_ok_label(concept: Dict[str, Any], dims: Dict[str, str]) -> bool:
        """Dimension rule for label-tier matches.

        Label-only rows (HTML table extraction) carry no XBRL dimensions,
        but the row label itself IS the segment evidence ("Products").
        So axis-requiring concepts are allowed; consolidated-only concepts
        stay gated (a dimensioned row can never be a total)."""
        req = concept.get("dimensions")
        if not req or req == "none":
            return not dims
        return True


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------

class DeterministicMapper:
    def __init__(self, schema: Dict[str, Any]):
        self.idx = SchemaIndex(schema)

    def map_unit(
        self, u: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return (mappings, rejected_entries) for one evidence unit."""
        mappings: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        dims = u.get("dimensions") or {}
        method = u.get("mapping_method")
        metric = u.get("metric")

        # --- derived (calculation) units: pass through if in the schema ---
        if method == "calculation":
            if metric in self.idx.derived_names:
                mappings.append(self._m(
                    u, metric, "derived", "calculation", 1.0, "passed", None))
            return mappings, rejected

        # --- Tier 1: xbrl_tag ------------------------------------------------
        concept_field = u.get("concept")
        b = bare(concept_field) if concept_field else None
        candidates = self.idx.tag_index.get(b, []) if b else []

        if candidates:
            allowed = [c for c in candidates if self.idx.dims_ok(c, dims)]
            denied = [c for c in candidates if not self.idx.dims_ok(c, dims)]
            # One rejected entry per unit, against the consolidated concept it
            # was most plausibly masquerading as. Segment-axis mismatches are
            # the same underlying reason (dimension_mismatch), so they are not
            # recorded as additional entries.
            consolidated_denied = [
                c for c in denied if c.get("dimensions") in (None, "", "none")
            ]
            if dims and consolidated_denied:
                rejected.append(self._r(
                    u, consolidated_denied[0]["canonical_name"],
                    "dimension_mismatch"))
            if allowed:
                best = self._pick(allowed, dims)
                mappings.append(self._m(
                    u, best["canonical_name"], "base", "xbrl_tag",
                    1.0, "passed", None))
                return mappings, rejected

        # --- Tier 2/3: exact_label, then normalized_label -------------------
        label = u.get("row_label") or ""
        if label:
            exact_hit = self.idx.exact_alias.get(norm(label))
            loose_hit = None if exact_hit else self.idx.loose_alias.get(loose(label))
            concept_name = exact_hit or loose_hit
            if concept_name:
                concept = self.idx.base_by_name(concept_name)
                if concept and self.idx.dims_ok_label(concept, dims):
                    map_method = "exact_label" if exact_hit else "normalized_label"
                    confidence = 0.9 if exact_hit else 0.7
                    # row-name-only matches stay pending until the math /
                    # context checks promote them (Group16 p.8 rule 3)
                    mappings.append(self._m(
                        u, concept_name, "base", map_method,
                        confidence, "pending", "row_name_only"))
        return mappings, rejected

    @staticmethod
    def _pick(allowed: List[Dict[str, Any]],
              dims: Dict[str, str]) -> Dict[str, Any]:
        """Disambiguate when one tag matches several concepts (the shared
        revenue tag: Revenue_total / Revenue_product / Revenue_service)."""
        if len(allowed) == 1:
            return allowed[0]
        members = " ".join(dims.values()).lower()
        for c in allowed:
            if c["canonical_name"] == "Revenue_product" and "product" in members:
                return c
        for c in allowed:
            if c["canonical_name"] == "Revenue_service" and "service" in members:
                return c
        seg = [c for c in allowed if c.get("dimensions") not in (None, "", "none")]
        return (seg or allowed)[0]

    @staticmethod
    def _m(u, concept, ctype, map_method, confidence, status, reason):
        return {
            "unit_id": u.get("unit_id"),
            "ticker": u.get("ticker"),
            "fiscal_year": u.get("fiscal_year"),
            "concept": concept,
            "concept_type": ctype,
            "confidence": confidence,
            "mapping_method": map_method,
            "validation_status": status,
            "reason": reason,
            "value": u.get("value"),
            "row_label": u.get("row_label"),
            "source_concept": u.get("concept"),
            "unit_type": u.get("unit_type"),
        }

    @staticmethod
    def _r(u, concept, reason):
        return {
            "unit_id": u.get("unit_id"),
            "ticker": u.get("ticker"),
            "fiscal_year": u.get("fiscal_year"),
            "concept": concept,
            "mapping_method": "xbrl_tag",
            "validation_status": "rejected",
            "reason": reason,
            "value": u.get("value"),
            "row_label": u.get("row_label"),
            "dimensions": u.get("dimensions"),
        }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_mapper(
    units: Optional[List[Dict[str, Any]]] = None,
    schema_path: Path = SCHEMA_PATH,
) -> Dict[str, Any]:
    schema = yaml_load(schema_path)
    if units is None:
        units = load_units(UNITS_PATH)
    mapper = DeterministicMapper(schema)
    mappings, rejected, unmapped = [], [], []
    for u in units:
        m, r = mapper.map_unit(u)
        mappings.extend(m)
        rejected.extend(r)
        if not m and not r:
            unmapped.append(u)
    stats = {
        "units": len(units),
        "mappings": len(mappings),
        "rejected": len(rejected),
        "unmapped": len(unmapped),
        "skipped_derived_duplicates": sum(
            1 for u in unmapped
            if str(u.get("concept") or "").startswith("derived:")
        ),
        "by_status": dict(Counter(m["validation_status"] for m in mappings)),
        "by_method": dict(Counter(m["mapping_method"] for m in mappings)),
        "rejected_by_reason": dict(Counter(r["reason"] for r in rejected)),
    }
    return {"schema": schema, "mappings": mappings, "rejected": rejected,
            "unmapped": unmapped, "stats": stats}


# ---------------------------------------------------------------------------
# Acceptance vs. the AAPL/MSFT verified company dictionary
# ---------------------------------------------------------------------------

def acceptance_check(bundle: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Every `accepted` entry in the verified company dictionary must appear
    as a passed mapping; every segment side must appear as a rejected
    candidate (dimension_mismatch) AND as a passed segment mapping.
    Returns (failures, notes)."""
    fails: List[str] = []
    notes: List[str] = []
    mappings, rejected = bundle["mappings"], bundle["rejected"]
    companies = (bundle["schema"].get("companies") or {})

    def has_map(ticker, concept, value, status="passed"):
        return any(
            m["ticker"] == ticker and m["concept"] == concept
            and m["validation_status"] == status and m["value"] == value
            for m in mappings
        )

    for ticker, entry in companies.items():
        for name, c in entry["concepts"].items():
            if name == "Revenue_segment":
                for side in ("product", "service"):
                    s = c[side]
                    ok_rej = any(
                        r["ticker"] == ticker and r["value"] == s["value"]
                        and r["reason"] == "dimension_mismatch"
                        for r in rejected
                    )
                    ok_map = has_map(ticker, f"Revenue_{side}", s["value"])
                    if ok_rej and ok_map:
                        print(f"  PASS  {ticker} {side}: rejected_as_total + "
                              f"mapped Revenue_{side} ({s['value']:,})")
                    else:
                        fails.append(f"{ticker} {side}: rej={ok_rej} map={ok_map}")
            elif c.get("status") == "accepted" and c.get("value") is not None:
                if has_map(ticker, name, c["value"]):
                    print(f"  PASS  {ticker} {name}: {c['value']:,}")
                else:
                    # distinguish "mapper missed it" from "A's dataset lacks it"
                    in_data = any(
                        u_value == c["value"] for u_value in
                        (u.get("value") for u in bundle["unmapped"])
                    )
                    if in_data:
                        fails.append(f"{ticker} {name}: in units but not mapped")
                    else:
                        notes.append(f"{ticker} {name}: value {c['value']:,} "
                                     f"not in Person A's dataset (needs re-ingest)")
    return fails, notes


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
    bundle = run_mapper()
    stats = bundle["stats"]
    print(f"Loaded {stats['units']} units -> {stats['mappings']} mappings, "
          f"{stats['rejected']} rejected entries, {stats['unmapped']} unmapped")
    print("  mappings by status:", stats["by_status"])
    print("  mappings by method:", stats["by_method"])
    print("  rejected by reason:", stats["rejected_by_reason"])

    print("\n== Sample: AAPL revenue (tag tier + dimension gate) ==")
    for m in bundle["mappings"]:
        if m["ticker"] == "AAPL" and m["concept"] in (
                "Revenue_total", "Revenue_product", "Revenue_service"):
            print(f"  {m['concept']:<16} {m['validation_status']:<8} "
                  f"{m['mapping_method']:<12} value={m['value']:>15,} "
                  f"row={m['row_label']!r}")
    print("  -- rejected entries (AAPL, Revenue_total candidates) --")
    for r in bundle["rejected"]:
        if r["ticker"] == "AAPL" and r["concept"] == "Revenue_total":
            print(f"  rejected value={r['value']:>15,} row={r['row_label']!r} "
                  f"dims={list((r['dimensions'] or {}).values())} "
                  f"reason={r['reason']}")
            break

    print("\n== Acceptance vs verified AAPL/MSFT dictionary ==")
    fails, notes = acceptance_check(bundle)
    for n in notes:
        print(f"  NOTE  {n}")
    if fails:
        print(f"\nFAIL: {len(fails)} acceptance problem(s)")
        for f in fails:
            print(f"  - {f}")
    else:
        print("\nALL ACCEPTANCE CHECKS PASSED")

    out = _out_dir()
    (out / "mappings.jsonl").write_text(
        "\n".join(json.dumps(m, default=str) for m in bundle["mappings"]) + "\n",
        encoding="utf-8")
    (out / "rejected_mappings.jsonl").write_text(
        "\n".join(json.dumps(r, default=str) for r in bundle["rejected"]) + "\n",
        encoding="utf-8")
    print(f"\nWrote mappings/rejected_mappings -> {out}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
