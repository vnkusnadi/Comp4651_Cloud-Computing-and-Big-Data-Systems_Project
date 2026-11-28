"""
Person D: Spark vs plain Python for Person B's step (map numbers to the
dictionary, build cross-company pairs, group into the 3 question types).

    python -m evaluation.spark_vs_python                 # 5 companies, 30 companies, 30 companies x 10
    python -m evaluation.spark_vs_python --sizes 1 10 --cores 1 4

Both sides run the SAME functions from src/spark/stage45_spark.py, so the only
difference is who runs them: one Python process, or Spark with 1 or N cores.
"Copies" repeats the 30-company data with shifted years to imitate a bigger
dataset (more companies x more years), since the real data is small.

Writes evaluation/results/spark_vs_python.json.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from src.spark import stage45_spark as s45
from src.util.yaml import load as load_yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
UNITS_5 = REPO / "data_ingestion_and_processing" / "data" / "evidence_units" / "evidence_units.jsonl"
UNITS_30 = REPO / "data_ingestion_and_processing" / "data" / "evidence_units_it30" / "evidence_units.jsonl"


def _lines(path: Path, copies: int) -> List[str]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if copies > 1:
        base = [json.loads(l) for l in lines]
        lines = [json.dumps({**u, "unit_id": f"{u['unit_id']}_{k}", "fiscal_year": u["fiscal_year"] - 10 * k})
                 for k in range(copies) for u in base]
    return lines


def plain_python(lines: List[str]) -> Dict[str, Any]:
    schema = load_yaml(s45.SCHEMA)
    cats = {c["name"]: list(c["members"]) for c in schema["categories"]}
    m2c: Dict[str, List[str]] = {}
    for c, ms in cats.items():
        for m in ms:
            m2c.setdefault(m, []).append(c)
    alias = {b.get("metric", b["canonical_name"]): b["canonical_name"] for b in schema["base_concepts"]}

    t0 = time.perf_counter()
    mapped = list(s45._map_partition(lines, schema))
    passed = [x for x in mapped if x["kind"] == "unit" and x.get("validation_status") == "passed"]
    by_key = defaultdict(list)
    for u in passed:
        by_key[(u["metric"], u["fiscal_year"])].append(u)
    edges = [e for k, us in by_key.items() for e in s45._edges_for_group(k, us)]
    groups = defaultdict(list)
    for u in passed:
        canon = alias.get(u["metric"], u["metric"])
        for c in m2c.get(canon, []):
            groups[(u["ticker"], u["fiscal_year"], c)].append(canon)
    bundles = [s45._bundle_for_group(k, ms, cats[k[2]]) for k, ms in groups.items()]
    return {"seconds": round(time.perf_counter() - t0, 3), "alignment_edges": len(edges),
            "category_bundles": len(bundles)}


def spark(lines: List[str], cores: int) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "units.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        t0 = time.perf_counter()
        r = s45.run(master=f"local[{cores}]", partitions=max(2, cores * 2), units_path=p, out_dir=None)
        wall = time.perf_counter() - t0
    st = r["stats"]
    return {"seconds": st["seconds"]["total"], "seconds_with_startup": round(wall, 3),
            "alignment_edges": st["alignment_edges"], "category_bundles": st["category_bundles"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", nargs="+", type=int, default=[1, 10],
                    help="copies of the 30-company data (the 5-company data always runs first)")
    ap.add_argument("--cores", nargs="+", type=int, default=[1, 4])
    a = ap.parse_args()
    datasets = [("5 companies, 2025", UNITS_5, 1)]
    datasets += [(f"30 companies, 2020-2025{'' if k == 1 else f' x{k}'}", UNITS_30, k) for k in a.sizes]
    out = []
    for name, path, k in datasets:
        lines = _lines(path, k)
        row: Dict[str, Any] = {"data": name, "saved_numbers": len(lines), "python": plain_python(lines)}
        for c in a.cores:
            row[f"spark_{c}_cores"] = spark(lines, c)
        out.append(row)
        print(f"{name:<32} {len(lines):>8} numbers  python {row['python']['seconds']:>7}s  " +
              "  ".join(f"spark[{c}] {row[f'spark_{c}_cores']['seconds']:>7}s" for c in a.cores))
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "spark_vs_python.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Saved -> evaluation/results/spark_vs_python.json")


if __name__ == "__main__":
    main()
