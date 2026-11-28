"""
Person B stages on Spark: map evidence units to dictionary terms (Stage 4),
build cross-company alignment edges (Stage 5) and group the passed numbers into
the 3 question types (category bundles), as a map -> shuffle -> reduce job.

    python -m src.spark.stage45_spark                       # local[*]
    python -m src.spark.stage45_spark --master local[1]     # one core, for the speed test
    python -m src.spark.stage45_spark --copies 20           # 20x the data, scale test

Map:     each partition builds Kelvin's DeterministicMapper once and maps its units.
Shuffle: passed units are keyed by (metric, fiscal_year) and by (ticker, fiscal_year, category).
Reduce:  per key, Kelvin's build_alignment_edges and a coverage count.

The output is the same as the plain-Python run (src/mapping/deterministic.py
+ src/align/validate.py); tests/test_spark_stage45.py checks that.
Writes data/spark/{mappings,rejected_mappings,alignment_edges,category_bundles}.jsonl
and data/spark/run_stats.json (time per stage, partitions, rows).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO = Path(__file__).resolve().parents[2]
UNITS = REPO / "data_ingestion_and_processing" / "data" / "evidence_units" / "evidence_units.jsonl"
SCHEMA = REPO / "configs" / "sectors" / "information_technology.yaml"
OUT = REPO / "data" / "spark"


def _map_partition(rows: Iterable[str], schema: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    from src.mapping.deterministic import DeterministicMapper

    mapper = DeterministicMapper(schema)          # built once per partition, not per row
    for line in rows:
        u = json.loads(line)
        m, r = mapper.map_unit(u)
        for x in m:
            yield {"kind": "mapping", **x}
        for x in r:
            yield {"kind": "rejected", **x}
        yield {"kind": "unit", **u}


def _edges_for_group(key, units: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from src.align.validate import build_alignment_edges

    metric, fy = key
    return build_alignment_edges(list(units), metric, fy)


def _bundle_for_group(key, metrics: Iterable[str], members: List[str]) -> Dict[str, Any]:
    ticker, fy, category = key
    have = sorted(set(metrics) & set(members))
    return {"ticker": ticker, "fiscal_year": fy, "category": category,
            "metrics_found": have, "found": len(have), "of": len(members),
            "missing": sorted(set(members) - set(have))}


def run(master: str = "local[*]", partitions: Optional[int] = None, copies: int = 1,
        units_path: Path = UNITS, out_dir: Optional[Path] = OUT) -> Dict[str, Any]:
    from pyspark.sql import SparkSession

    from src.util.yaml import load as load_yaml

    schema = load_yaml(SCHEMA)
    cats = {c["name"]: list(c["members"]) for c in schema["categories"]}
    metric_to_cats: Dict[str, List[str]] = {}
    for c, ms in cats.items():
        for m in ms:
            metric_to_cats.setdefault(m, []).append(c)
    # base concept canonical name -> unit "metric" field (e.g. R&D_expense -> ResearchAndDevelopmentExpense)
    metric_alias = {b.get("metric", b["canonical_name"]): b["canonical_name"] for b in schema["base_concepts"]}

    spark = (SparkSession.builder.master(master).appName("sectorview-stage45")
             .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "8")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    sc = spark.sparkContext
    stats: Dict[str, Any] = {"master": master, "copies": copies}
    try:
        t0 = time.perf_counter()
        lines = [l for l in units_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if copies > 1:                                # scale test: same filings, renamed years
            base = [json.loads(l) for l in lines]
            lines = [json.dumps({**u, "unit_id": f"{u['unit_id']}_{k}", "fiscal_year": u["fiscal_year"] - k})
                     for k in range(copies) for u in base]
        n_parts = partitions or max(2, sc.defaultParallelism)
        rdd = sc.parallelize(lines, n_parts)
        b_schema = sc.broadcast(schema)

        # ---- Stage 4: map ---------------------------------------------------
        mapped = rdd.mapPartitions(lambda it: _map_partition(it, b_schema.value)).cache()
        mappings = mapped.filter(lambda x: x["kind"] == "mapping").collect()
        rejected = mapped.filter(lambda x: x["kind"] == "rejected").collect()
        t1 = time.perf_counter()

        # ---- Stage 5: shuffle by (metric, fy) -> reduce to alignment edges ---
        passed = mapped.filter(lambda x: x["kind"] == "unit" and x.get("validation_status") == "passed")
        edges = (passed.map(lambda u: ((u["metric"], u["fiscal_year"]), u))
                 .groupByKey()
                 .flatMap(lambda kv: _edges_for_group(kv[0], kv[1]))
                 .collect())
        t2 = time.perf_counter()

        # ---- category bundles: shuffle by (ticker, fy, category) -> coverage --
        b_m2c, b_alias, b_cats = sc.broadcast(metric_to_cats), sc.broadcast(metric_alias), sc.broadcast(cats)
        bundles = (passed
                   .map(lambda u: (u, b_alias.value.get(u["metric"], u["metric"])))
                   .flatMap(lambda ua: [((ua[0]["ticker"], ua[0]["fiscal_year"], c), ua[1])
                                        for c in b_m2c.value.get(ua[1], [])])
                   .groupByKey()
                   .map(lambda kv: _bundle_for_group(kv[0], kv[1], b_cats.value[kv[0][2]]))
                   .collect())
        t3 = time.perf_counter()
        stats.update({
            "partitions": n_parts, "units": len(lines),
            "mappings": len(mappings), "rejected": len(rejected),
            "alignment_edges": len(edges), "category_bundles": len(bundles),
            "seconds": {"stage4_map": round(t1 - t0, 3), "stage5_align": round(t2 - t1, 3),
                        "bundles": round(t3 - t2, 3), "total": round(t3 - t0, 3)},
        })
    finally:
        spark.stop()

    strip = lambda d: {k: v for k, v in d.items() if k != "kind"}  # noqa: E731
    result = {"mappings": [strip(m) for m in mappings], "rejected": [strip(r) for r in rejected],
              "alignment_edges": edges, "category_bundles": sorted(
                  bundles, key=lambda b: (b["ticker"], b["fiscal_year"], b["category"])), "stats": stats}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in ("mappings", "rejected", "alignment_edges", "category_bundles"):
            fname = "rejected_mappings" if name == "rejected" else name
            (out_dir / f"{fname}.jsonl").write_text(
                "".join(json.dumps(x, default=str) + "\n" for x in result[name]), encoding="utf-8")
        (out_dir / "run_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", default="local[*]")
    ap.add_argument("--partitions", type=int, default=None)
    ap.add_argument("--copies", type=int, default=1, help="repeat the data N times (scale test)")
    a = ap.parse_args()
    r = run(a.master, a.partitions, a.copies)
    print(json.dumps(r["stats"], indent=2))
