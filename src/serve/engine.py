"""
Person C — Stage 7: retrieval -> evidence selection -> claim-evidence matrix.

Pipeline for one query (spec §4.2 Stage 7):
  1. parse   : sector / companies / concepts / period / intent
  2. retrieve: hybrid BM25 + dense, filtered by metadata
               (company, sector, concept, period, has_dimension, validation_status)
  3. expand  : alignment_edges (Person B) and linked_footnote_ids
  4. derive  : formula-backed metrics from validated base units
               (arithmetic is code, never the LLM)
  5. generate: ONLY after evidence selection. Text is rendered from the selected
               evidence; every claim carries evidence_unit_ids.

The LLM is never the sole calculator or aligner: numbers come from units,
ratios from schema formulas, alignment from Person B's validated edges.
"""

from __future__ import annotations

import glob
import json
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.align.validate import (
    CONSOLIDATED_METRICS,
    PRIMARY_STATEMENT_MARKERS,
    build_alignment_edges,
    dims_of,
    load_units,
)
from src.index.bm25 import BM25Index, unit_text
from src.index.dense import DenseIndex
from src.serve.query_parser import COMPANY_ALIASES, ParsedQuery, QueryParser
from src.util.formula import _formula_names, _safe_formula, eval_formula
from src.util.yaml import load as load_yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = REPO_ROOT / "configs" / "sectors" / "information_technology.yaml"
DEFAULT_UNITS = (REPO_ROOT / "data_ingestion_and_processing" / "data"
                 / "evidence_units" / "evidence_units.jsonl")
EDGE_GLOB = str(REPO_ROOT / "data" / "alignment_edges*.jsonl")

RRF_K = 60


def has_dimension(u: Dict[str, Any]) -> bool:
    return bool(dims_of(u))


def edgar_urls(u: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Link back to the filing on SEC EDGAR (index page of the accession)."""
    cik, acc = u.get("cik"), u.get("accession_number")
    if not cik or not acc:
        return {"filing_index": None}
    folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
    return {"filing_index": f"{folder}/{acc}-index.htm"}


def fmt_value(v: Optional[float], unit: Optional[str]) -> str:
    if v is None:
        return "n/a"
    if unit == "ratio":
        return f"{v:.2%}"
    if unit == "USD" and abs(v) >= 1e6:
        return f"${v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.1f}M"
    return f"{v:,.2f}" + (f" {unit}" if unit else "")


class Engine:
    def __init__(
        self,
        units_path: str | Path = DEFAULT_UNITS,
        schema_path: str | Path = DEFAULT_SCHEMA,
        edges_glob: Optional[str] = EDGE_GLOB,
    ):
        self.schema = load_yaml(schema_path)
        self.parser = QueryParser(self.schema)
        self.units = load_units(units_path)
        self.by_id = {u["unit_id"]: u for u in self.units}
        self.names = {t: n for n, t in COMPANY_ALIASES.items()}
        self.base = {c["canonical_name"]: c for c in self.schema["base_concepts"]}
        self.derived = {d["canonical_name"]: d for d in self.schema["derived_metrics"]}
        self.categories = {c["name"]: c for c in self.schema["categories"]}

        # --- Stage 6 indexes: unit text enriched with concept metadata --------
        meta_by_metric = {c.get("metric"): c for c in self.schema["base_concepts"]}
        self.texts = [unit_text(u, meta_by_metric.get(u.get("metric"))) for u in self.units]
        self.bm25 = BM25Index(self.units, self.texts)
        self.dense = DenseIndex(self.units, self.texts)

        self._edge_rows = self._load_edges(edges_glob)
        self._edge_cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------ stats
    def index_stats(self) -> Dict[str, Any]:
        return {
            "num_units": len(self.units),
            "bm25": {"build_time_s": round(self.bm25.build_time, 4),
                     "index_size_bytes": self.bm25.index_size},
            "dense": {"backend": self.dense.backend_name,
                      "build_time_s": round(self.dense.build_time, 4),
                      "index_size_bytes": self.dense.index_size},
            "alignment_edges_source": "file" if self._edge_rows else "computed_on_demand",
        }

    def save_stats(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.index_stats(), indent=2), encoding="utf-8")

    # --------------------------------------------------------------- helpers
    def metric_of(self, canonical: str) -> Optional[str]:
        c = self.base.get(canonical)
        return c.get("metric", canonical) if c else None

    def latest_fy(self, ticker: str) -> Optional[int]:
        fys = [u["fiscal_year"] for u in self.units
               if u.get("ticker") == ticker and u.get("fiscal_year")]
        return max(fys) if fys else None

    def _concept_query(self, canonical: str, parsed: ParsedQuery) -> str:
        c = self.base.get(canonical, {})
        return " ".join([parsed.query, canonical, *(c.get("aliases") or [])])

    def candidate_ids(self, ticker: str, metric: str, fy: int,
                      want_dimension: Optional[bool], status: Optional[str] = "passed") -> set:
        """Metadata pre-filter (the has_dimension=false filter lives here)."""
        out = set()
        for i, u in enumerate(self.units):
            if u.get("ticker") != ticker or u.get("metric") != metric or u.get("fiscal_year") != fy:
                continue
            if status and u.get("validation_status") != status:
                continue
            if want_dimension is not None and has_dimension(u) != want_dimension:
                continue
            out.add(i)
        return out

    def hybrid_rank(self, query: str, allowed: set, k: int = 10) -> List[Dict[str, Any]]:
        """BM25 + dense fused with reciprocal-rank fusion, within `allowed`."""
        if not allowed:
            return []
        lex = self.bm25.search(query, k=len(allowed), allowed=allowed)
        den = self.dense.search(query, k=len(allowed), allowed=allowed)
        fused: Dict[int, float] = {i: 0.0 for i in allowed}
        for rank, (i, _) in enumerate(lex):
            fused[i] += 1.0 / (RRF_K + rank + 1)
        for rank, (i, _) in enumerate(den):
            fused[i] += 1.0 / (RRF_K + rank + 1)
        bm = dict(lex)
        dn = dict(den)

        def tier(u: Dict[str, Any]) -> int:
            # Person B's source-quality preference breaks ties among passed units:
            # XBRL-tag mapping on a primary statement > XBRL > anything else.
            primary = any(m in (u.get("section_path") or "").lower()
                          for m in PRIMARY_STATEMENT_MARKERS)
            if u.get("mapping_method") == "xbrl_tag":
                return 0 if primary or not u.get("section_path") else 1
            return 2

        rows = sorted(allowed, key=lambda i: i)
        rows.sort(key=lambda i: self.units[i].get("filing_date") or "", reverse=True)  # newest filing first
        rows.sort(key=lambda i: (tier(self.units[i]), -fused[i]))                      # stable
        return [{"idx": i, "rrf": fused[i], "bm25": bm.get(i, 0.0), "dense": dn.get(i, 0.0)}
                for i in rows[:k]]

    def select_unit(self, ticker: str, canonical: str, fy: int,
                    parsed: ParsedQuery) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        metric = self.metric_of(canonical)
        concept = self.base.get(canonical, {})
        consolidated = (concept.get("dimensions") == "none") or (metric in CONSOLIDATED_METRICS)
        allowed = self.candidate_ids(ticker, metric, fy, False if consolidated else None)
        ranked = self.hybrid_rank(self._concept_query(canonical, parsed), allowed)
        trace = {
            "ticker": ticker, "concept": canonical, "metric": metric, "fiscal_year": fy,
            "filters": {"sector": parsed.sector, "validation_status": "passed",
                        "has_dimension": False if consolidated else "any"},
            "n_candidates": len(allowed),
            "top": [{"unit_id": self.units[r["idx"]]["unit_id"],
                     "rrf": round(r["rrf"], 5), "bm25": round(r["bm25"], 3),
                     "dense": round(r["dense"], 3)} for r in ranked[:3]],
        }
        if not ranked:
            return None, trace
        return self.units[ranked[0]["idx"]], trace

    def blocked_candidates(self, ticker: str, canonical: str, fy: int, selected: Optional[dict],
                           limit: int = 4) -> List[Dict[str, Any]]:
        """Units for the same concept that were NOT used (segment rows, rejected)
        — the 'total vs. segment revenue' distractors, shown for audit."""
        metric = self.metric_of(canonical)
        seen, out = set(), []
        for u in self.units:
            if (u.get("ticker"), u.get("metric"), u.get("fiscal_year")) != (ticker, metric, fy):
                continue
            if selected is not None and u["unit_id"] == selected["unit_id"]:
                continue
            if not has_dimension(u) and (
                    u.get("validation_status") == "passed"
                    or (selected is not None and u.get("value") == selected.get("value"))):
                continue                                  # equivalent duplicate, not a distractor
            reason = ("dimension_mismatch" if has_dimension(u)
                      else f"validation_{u.get('validation_status')}")
            key = (u.get("row_label"), u.get("value"), reason)
            if key in seen:
                continue
            seen.add(key)
            out.append({"unit_id": u["unit_id"], "ticker": ticker, "concept": canonical,
                        "row_label": u.get("row_label"), "value": u.get("value"),
                        "dimensions": dims_of(u), "reason": reason,
                        "validation_status": u.get("validation_status")})
        out.sort(key=lambda b: -(abs(b["value"]) if isinstance(b["value"], (int, float)) else 0))
        return out[:limit]

    # ----------------------------------------------------------- alignment IO
    @staticmethod
    def _load_edges(pattern: Optional[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in sorted(glob.glob(pattern)) if pattern else []:
            with open(path, encoding="utf-8") as fh:
                rows.extend(json.loads(l) for l in fh if l.strip())
        return rows

    def edges_for(self, canonical: str, fy: int, tickers: List[str]) -> List[Dict[str, Any]]:
        """Read interface to Person B's alignment_edges (file first, else computed)."""
        metric = self.metric_of(canonical)
        rows = [e for e in self._edge_rows
                if e.get("concept") in (canonical, metric) and e.get("period") == f"FY{fy}"]
        if not rows:
            key = (metric, fy)
            if key not in self._edge_cache:
                self._edge_cache[key] = build_alignment_edges(self.units, metric, fy)
            rows = self._edge_cache[key]
        return [e for e in rows
                if e.get("unit_a_ticker") in tickers and e.get("unit_b_ticker") in tickers]

    # ------------------------------------------------------------------ main
    def answer(self, query: str) -> Dict[str, Any]:
        t0 = time.perf_counter()
        parsed = self.parser.parse(query)
        resp: Dict[str, Any] = {
            "query": query, "parsed": parsed.to_dict(), "conclusion": "", "claims": [],
            "evidence": [], "alignment": [], "blocked_candidates": [],
            "diagnostics": {"retrieval": [], "unsupported": [], "notes": list(parsed.notes)},
        }
        if parsed.intent == "unsupported":
            resp["conclusion"] = ("I could not map this question to a concept in the "
                                  f"{parsed.sector} schema, so I did not answer.")
            resp["diagnostics"]["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return resp

        claims: List[Dict[str, Any]] = []
        used: Dict[str, Dict[str, Any]] = {}            # unit_id -> unit actually cited
        lines: List[str] = []
        concepts_for_alignment: set = set()
        fy_used: Dict[str, Optional[int]] = {}

        def claim(kind, text, evidence_ids, **extra):
            c = {"claim_id": f"c{len(claims) + 1}", "kind": kind, "text": text,
                 "evidence_unit_ids": list(dict.fromkeys(evidence_ids)), **extra}
            claims.append(c)
            return c

        for metric_name in parsed.metrics:
            values: Dict[str, Dict[str, Any]] = {}       # ticker -> {value, unit_ids, unit}
            if metric_name in self.derived:
                d = self.derived[metric_name]
                den = d.get("denominator")
                # Every name in the formula, e.g. (OperatingCashFlow - abs(Capex)) / Revenue_total.
                # (Before: only numerator/denominator were used, so free cash flow
                # silently ignored Capex.) Formulas built on other derived metrics
                # (EBITDA_margin) are left to the category fact sheet.
                operands = [n.replace("_and_", "&") for n in _formula_names(_safe_formula(d["formula"]))]
                if any(op not in self.base for op in operands):
                    resp["diagnostics"]["unsupported"].append(
                        {"metric": metric_name, "reason": "formula_uses_a_derived_metric"})
                    continue
            elif metric_name in self.base:
                d, operands = None, [metric_name]
            else:
                resp["diagnostics"]["unsupported"].append(
                    {"metric": metric_name, "reason": "not_in_schema_base_or_derived"})
                continue

            for tk in parsed.companies:
                fy = parsed.fiscal_year or self.latest_fy(tk)
                fy_used[tk] = fy
                name = self.names.get(tk, tk).title()
                picks = {}
                for op in operands:
                    u, trace = (self.select_unit(tk, op, fy, parsed) if fy else (None, {}))
                    resp["diagnostics"]["retrieval"].append(trace)
                    picks[op] = u
                    if u is not None:
                        used[u["unit_id"]] = u
                        resp["blocked_candidates"].extend(self.blocked_candidates(tk, op, fy, u))
                    concepts_for_alignment.add(op)

                missing = [op for op, u in picks.items() if u is None]
                if missing:
                    claim("coverage",
                          f"No validated evidence for {name} {', '.join(missing)} in FY{fy}; "
                          f"{metric_name} is not reported for {name}.",
                          [], ticker=tk, metric=metric_name, status="insufficient_coverage")
                    continue

                if d is None:
                    u = picks[operands[0]]
                    val, unit = float(u["value"]), u.get("unit")
                    values[tk] = {"value": val, "unit": unit, "ids": [u["unit_id"]]}
                    claim("fact", f"{name} FY{fy} {metric_name}: {fmt_value(val, unit)} "
                                  f"({u.get('row_label')}).",
                          [u["unit_id"]], ticker=tk, metric=metric_name, value=val, unit=unit)
                else:
                    ids = [picks[op]["unit_id"] for op in operands]
                    vals = {op: float(picks[op]["value"]) for op in operands}
                    if den in vals and vals[den] <= 0:  # denominator > 0 constraint (spec Tier 2)
                        claim("coverage", f"{name} {metric_name} rejected: denominator "
                                          f"{den} is not positive.", ids,
                              ticker=tk, metric=metric_name, status="denominator_nonpositive")
                        continue
                    val = eval_formula(d["formula"], vals)
                    vunit = "ratio" if "/" in d["formula"] else (picks[operands[0]].get("unit") or "USD")
                    values[tk] = {"value": val, "unit": vunit, "ids": ids}
                    shown = ", ".join(f"{op} {fmt_value(v, picks[op].get('unit'))}" for op, v in vals.items())
                    claim("derived",
                          f"{name} FY{fy} {metric_name} = {d['formula']} with {shown} "
                          f"= {fmt_value(val, vunit)}.",
                          ids, ticker=tk, metric=metric_name, value=val, unit=vunit,
                          formula_id=d.get("formula_id"), formula=d.get("formula"),
                          inputs={op: picks[op]["unit_id"] for op in operands})

            if len(values) >= 2:                          # comparison needs both sides' evidence
                ranked = sorted(values.items(), key=lambda kv: -kv[1]["value"])
                (top_t, top), (low_t, low) = ranked[0], ranked[-1]
                unit = top["unit"]
                text = (f"{self.names.get(top_t, top_t).title()} has the higher {metric_name} "
                        f"({fmt_value(top['value'], unit)}) vs. "
                        f"{self.names.get(low_t, low_t).title()} ({fmt_value(low['value'], unit)}).")
                if len(ranked) > 2:
                    text = (f"On {metric_name}, " + " > ".join(
                        f"{self.names.get(t, t).title()} {fmt_value(v['value'], unit)}"
                        for t, v in ranked) + ".")
                ev = [i for _, v in ranked for i in v["ids"]]
                claim("comparison", text, ev, metric=metric_name,
                      leader=top_t, ranking=[t for t, _ in ranked],
                      companies=[t for t, _ in ranked])
                lines.append(text)
            elif len(values) == 1:
                lines.append(claims[-1]["text"])
            elif not values:
                lines.append(f"{metric_name}: insufficient evidence.")

        # --- expand: alignment edges + linked footnotes ----------------------
        fy_any = next((v for v in fy_used.values() if v), None)
        selected_ids = set(used)
        if fy_any:
            for op in sorted(concepts_for_alignment):
                for e in self.edges_for(op, fy_any, parsed.companies):
                    resp["alignment"].append({
                        **e, "concept_canonical": op,
                        "uses_selected_units": e.get("unit_a") in selected_ids
                                               and e.get("unit_b") in selected_ids})
                    for side in ("unit_a", "unit_b"):
                        uid = e.get(side)
                        if uid in self.by_id and uid not in used:
                            used[uid] = self.by_id[uid]
        for u in list(used.values()):
            for fid in u.get("linked_footnote_ids") or []:
                if fid in self.by_id:
                    used.setdefault(fid, self.by_id[fid])

        cited = {i for c in claims for i in c["evidence_unit_ids"]}
        resp["evidence"] = [
            {**u, "has_dimension": has_dimension(u), "role": "cited" if uid in cited else "expanded",
             "source": {"page": u.get("source_page"), "table_id": u.get("table_id"),
                        "row_label": u.get("row_label"), "col_label": u.get("col_label"),
                        "section_path": u.get("section_path"), **edgar_urls(u)}}
            for uid, u in used.items()]
        resp["claims"] = claims
        resp["conclusion"] = " ".join(lines) if lines else "Insufficient evidence to answer."
        if parsed.intent == "category_analysis":
            resp["diagnostics"]["notes"].append(
                "Category questions return per-metric claims only; the Category Analysis Packet "
                "(Pareto, stability grid) and verified LLM verdict are Stage 5b/5c, not this service.")
        resp["diagnostics"]["fiscal_years_used"] = fy_used
        resp["diagnostics"]["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return resp


if __name__ == "__main__":                                  # python -m src.serve.engine
    eng = Engine()
    eng.save_stats(REPO_ROOT / "data" / "index_stats.json")
    print(json.dumps(eng.index_stats(), indent=2))
