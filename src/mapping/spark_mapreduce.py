"""
Person B — Spark MapReduce for Stage 4 mapping + Stage 5 validation.

The algorithm is unchanged from the single-machine pipeline; only the
execution engine changes:

    Map    : per-unit three-tier mapping + dimension gate
             (deterministic.DeterministicMapper.map_unit — pure function)
    Filter : dimension gate (inside map_unit)
    Reduce : group by ticker  -> revenue reconciliation / accounting
             identities / R&D rules
             group by concept -> cross-company alignment edges
             (validate.revenue_reconciliation / check_identities /
             validate_rnd_expense / build_alignment_edges — pure functions)

Correctness is enforced by tests/test_spark_parity.py: the Spark output must
equal the single-machine output record-for-record (golden comparison).

Local simulation (no Spark required — works on any machine):
    python src/mapping/spark_mapreduce.py --local

Spark cluster:
    spark-submit src/mapping/spark_mapreduce.py --units <jsonl-or-parquet> --out <dir>

Note: this implementation collects the (small) results to the driver and
writes plain JSONL via the shared writer, so both engines produce
byte-identical artifacts. For the 10/50/179-filing scale experiment, switch
the writer to distributed parquet output (see `write_artifacts` comment).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.align.validate import (  # noqa: E402
    build_alignment_edges,
    check_identities,
    load_units,
    revenue_reconciliation,
    validate_rnd_expense,
)
from src.mapping.deterministic import (  # noqa: E402
    DeterministicMapper,
    UNITS_PATH,
)
from src.util.yaml import load as yaml_load  # noqa: E402

SCHEMA_PATH = _REPO_ROOT / "configs" / "sectors" / "information_technology.yaml"
DEFAULT_OUT = _REPO_ROOT / "data"
FISCAL_YEAR = 2025

# Concepts for which cross-company alignment edges are built (A's metric
# names, matching the single-machine demo).
EDGE_CONCEPTS = ("Revenue_total", "ResearchAndDevelopmentExpense")


def load_schema() -> Dict[str, Any]:
    return yaml_load(SCHEMA_PATH)


# ---------------------------------------------------------------------------
# REDUCE stage — pure functions shared by the local simulation and the Spark
# executors (same code runs in both engines).
# ---------------------------------------------------------------------------

def reduce_company(ticker: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-company math checks: revenue reconciliation, accounting
    identities, R&D rules. Pure function — runs identically on executors."""
    out: List[Dict[str, Any]] = []
    rec = revenue_reconciliation(rows, ticker, FISCAL_YEAR)
    if rec is not None:
        rec = dict(rec)
        rec["kind"] = "reconciliation"
        out.append(rec)
    for r in check_identities(rows, ticker, FISCAL_YEAR):
        r = dict(r)
        r["kind"] = "identity"
        out.append(r)
    for r in validate_rnd_expense(rows, ticker, FISCAL_YEAR):
        r = dict(r)
        r["kind"] = "rnd"
        out.append(r)
    return out


def reduce_concept(concept: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cross-company alignment edges for one concept."""
    return build_alignment_edges(rows, concept, FISCAL_YEAR)


# ---------------------------------------------------------------------------
# MAP stage — pure function; in Spark mode it runs on the executors via the
# broadcast schema, in local mode via a plain loop.
# ---------------------------------------------------------------------------

def _map_unit(u: Dict[str, Any], schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    mapper = DeterministicMapper(schema)
    ms, rs = mapper.map_unit(u)
    out: List[Dict[str, Any]] = []
    for m in ms:
        m = dict(m)
        m["kind"] = "mapping"
        out.append(m)
    for r in rs:
        r = dict(r)
        r["kind"] = "rejected"
        out.append(r)
    return out


def _strip_kind(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in rec.items() if k != "kind"}


# ---------------------------------------------------------------------------
# Local simulation — single-machine for-loop with the same map/reduce shape
# ---------------------------------------------------------------------------

def run_local(units_path: Path = UNITS_PATH) -> Dict[str, Any]:
    schema = load_schema()
    units = load_units(units_path)

    # ---- MAP ----
    mapped: List[Dict[str, Any]] = []
    for u in units:
        mapped.extend(_map_unit(u, schema))
    mappings = [_strip_kind(x) for x in mapped if x["kind"] == "mapping"]
    rejected = [_strip_kind(x) for x in mapped if x["kind"] == "rejected"]

    # ---- REDUCE: group by ticker, then by concept ----
    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for u in units:
        by_ticker.setdefault(u["ticker"], []).append(u)

    validation: List[Dict[str, Any]] = []
    for ticker in sorted(by_ticker):
        validation.extend(_strip_kind(r) for r in reduce_company(ticker, by_ticker[ticker]))

    edges: List[Dict[str, Any]] = []
    for concept in EDGE_CONCEPTS:
        edges.extend(reduce_concept(concept, units))

    stats = {
        "units": len(units),
        "mappings": len(mappings),
        "rejected": len(rejected),
        "validation_records": len(validation),
        "edges": len(edges),
    }
    return {"mappings": mappings, "rejected": rejected,
            "validation": validation, "edges": edges, "stats": stats}


# ---------------------------------------------------------------------------
# Spark pipeline
# ---------------------------------------------------------------------------

def run_spark(units_path: Path = UNITS_PATH, spark=None) -> Dict[str, Any]:
    """Run on Spark: broadcast the schema, map with flatMap, reduce with
    groupBy, then collect and return the SAME artifacts as run_local."""
    from pyspark.sql import SparkSession

    own_session = spark is None
    spark = spark or (SparkSession.builder
                      .appName("PersonB-Stage4-5")
                      .master("local[*]")
                      .getOrCreate())
    sc = spark.sparkContext
    schema_bc = sc.broadcast(load_schema())
    units = load_units(units_path)

    # Functions must live at module level for Spark to serialize them; the
    # broadcast variable is resolved on the executors.
    def _map(unit):
        return _map_unit(unit, schema_bc.value)

    def _reduce_company(kv):
        ticker, rows = kv
        return reduce_company(ticker, list(rows))

    def _reduce_concept(kv):
        concept, rows = kv
        return reduce_concept(concept, list(rows))

    n_slices = max(2, len(units) // 200)

    # ---- MAP: parallel over units ----
    mapped_rdd = sc.parallelize(units, n_slices).flatMap(_map).cache()

    # ---- REDUCE 1: group original units by ticker ----
    validation_rdd = (sc.parallelize(units, n_slices)
                        .groupBy(lambda u: u["ticker"])
                        .flatMap(_reduce_company))

    # ---- REDUCE 2: group accepted units by concept, pair companies ----
    edge_units = [u for u in units
                  if u.get("metric") in EDGE_CONCEPTS
                  and u.get("validation_status") == "passed"]
    edges_rdd = (sc.parallelize(edge_units, n_slices)
                   .groupBy(lambda u: u["metric"])
                   .flatMap(_reduce_concept))

    mappings = mapped_rdd.filter(lambda x: x["kind"] == "mapping") \
                         .map(_strip_kind).collect()
    rejected = mapped_rdd.filter(lambda x: x["kind"] == "rejected") \
                         .map(_strip_kind).collect()
    validation = validation_rdd.map(_strip_kind).collect()
    edges = edges_rdd.map(_strip_kind).collect()

    if own_session:
        spark.stop()

    stats = {
        "units": len(units),
        "mappings": len(mappings),
        "rejected": len(rejected),
        "validation_records": len(validation),
        "edges": len(edges),
    }
    return {"mappings": mappings, "rejected": rejected,
            "validation": validation, "edges": edges, "stats": stats}


# ---------------------------------------------------------------------------
# Shared writer (both engines produce identical artifacts)
# ---------------------------------------------------------------------------

def write_artifacts(out_dir: Path, result: Dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    def _write(name: str, records: List[Dict[str, Any]]) -> None:
        path = out_dir / name
        with path.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")

    _write("mappings.jsonl", result["mappings"])
    _write("rejected_mappings.jsonl", result["rejected"])
    _write("validation_report.jsonl", result["validation"])
    _write("alignment_edges.jsonl", result["edges"])
    return out_dir
    # SCALE NOTE: for the 10/50/179-filing experiment, replace the plain
    # JSONL writer with distributed writes, e.g.
    #   spark.createDataFrame(records).write.mode("overwrite").parquet(...)


def _out_dir(provided: Optional[str]) -> Path:
    if provided:
        return Path(provided)
    out = DEFAULT_OUT
    try:
        out.mkdir(exist_ok=True)
        probe = out / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        out = _REPO_ROOT.parent / "_person_b_out"
        out.mkdir(exist_ok=True)
        print(f"(repo data/ not writable here - writing to {out})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true",
                    help="single-machine simulation (no Spark required)")
    ap.add_argument("--units", default=str(UNITS_PATH),
                    help="evidence units jsonl/parquet")
    ap.add_argument("--out", default=None, help="output directory")
    args = ap.parse_args()

    if args.local:
        result = run_local(Path(args.units))
        engine = "local simulation"
    else:
        result = run_spark(Path(args.units))
        engine = "spark"

    s = result["stats"]
    print(f"engine={engine}")
    print(f"{s['units']} units -> {s['mappings']} mappings, "
          f"{s['rejected']} rejected, {s['validation_records']} validation "
          f"records, {s['edges']} edges")

    out = _out_dir(args.out)
    write_artifacts(out, result)
    print(f"wrote mappings / rejected_mappings / validation_report / "
          f"alignment_edges -> {out}")


if __name__ == "__main__":
    main()
