"""Person D: the Spark job for Person B's stages gives the same result as the
plain-Python run. Skipped when pyspark/Java are not installed."""

import pytest

pyspark = pytest.importorskip("pyspark")


def test_spark_matches_python(tmp_path):
    from src.align.validate import build_alignment_edges, load_units
    from src.mapping.deterministic import UNITS_PATH, run_mapper
    from src.spark.stage45_spark import run

    r = run(master="local[2]", partitions=3, out_dir=tmp_path)
    py = run_mapper()
    key = lambda m: (m["unit_id"], m["concept"], m["validation_status"])  # noqa: E731
    assert sorted(map(key, r["mappings"])) == sorted(map(key, py["mappings"]))
    assert sorted(map(key, r["rejected"])) == sorted(map(key, py["rejected"]))

    units = load_units(UNITS_PATH)
    passed = [u for u in units if u.get("validation_status") == "passed"]
    expected = []
    for metric, fy in sorted({(u["metric"], u["fiscal_year"]) for u in passed}):
        expected += build_alignment_edges([u for u in passed if u["metric"] == metric and u["fiscal_year"] == fy], metric, fy)
    assert sorted(e["edge_id"] for e in r["alignment_edges"]) == sorted(e["edge_id"] for e in expected)
    assert (tmp_path / "run_stats.json").exists()
    rev = [b for b in r["category_bundles"] if b["ticker"] == "AAPL" and b["category"] == "Scale"][0]
    assert "Revenue_total" in rev["metrics_found"]
