"""
Person B — parity tests for the Spark MapReduce implementation.

The Spark pipeline must produce byte-identical artifacts to the
single-machine reference implementation (golden comparison):

  * mappings / rejected            == src.mapping.deterministic.run_mapper()
  * validation / alignment edges   == the validate.py functions

Runs standalone (the Spark test SKIPs when pyspark is not installed):
    python tests/test_spark_parity.py
or under pytest (from the repository root):
    python -m pytest tests/test_spark_parity.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.align.validate import build_alignment_edges  # noqa: E402
from src.mapping.deterministic import run_mapper  # noqa: E402
from src.mapping.spark_mapreduce import (  # noqa: E402
    EDGE_CONCEPTS,
    run_local,
    run_spark,
)

_LOCAL = None
SKIPPED = []


def _load():
    global _LOCAL
    if _LOCAL is None:
        _LOCAL = run_local()
    return _LOCAL


def _java_available() -> bool:
    import os
    import shutil
    return shutil.which("java") is not None or bool(os.environ.get("JAVA_HOME"))


def _skip(name: str, reason: str) -> None:
    print(f"SKIP  {name} ({reason})")
    SKIPPED.append(name)


def _snapshot(records) -> list:
    return sorted(json.dumps(r, sort_keys=True, default=str) for r in records)


def test_local_mappings_match_deterministic_reference():
    """The Map phase (simulated) must equal the single-machine mapper
    record-for-record."""
    local = _load()
    reference = run_mapper()
    assert _snapshot(local["mappings"]) == _snapshot(reference["mappings"]), (
        f"mappings diverge: {local['stats']['mappings']} vs "
        f"{reference['stats']['mappings']}"
    )
    assert _snapshot(local["rejected"]) == _snapshot(reference["rejected"]), (
        f"rejected diverge: {local['stats']['rejected']} vs "
        f"{reference['stats']['rejected']}"
    )


def test_local_reduce_produces_expected_validation_records():
    """Reduce phase: per-company reconciliation + 3 identities each +
    R&D checks, all present."""
    local = _load()
    kinds = {}
    for r in local["validation"]:
        kinds[r.get("check") or r.get("reason") or "rnd"] = \
            kinds.get(r.get("check") or r.get("reason") or "rnd", 0) + 1
    # 5 companies x (1 reconciliation + 3 identities) + R&D records
    assert sum(1 for r in local["validation"] if r.get("check") == "segment_sum_to_total") == 5
    assert sum(1 for r in local["validation"] if r.get("check") == "gross_profit_identity") == 5
    assert sum(1 for r in local["validation"] if r.get("check") == "operating_income_identity") == 5
    assert sum(1 for r in local["validation"] if r.get("check") == "eps_identity") == 5
    assert sum(1 for r in local["validation"] if "status" in r and r.get("unit_id")) > 0  # R&D rows
    # reconciliation must pass for all five companies
    recs = [r for r in local["validation"] if r.get("check") == "segment_sum_to_total"]
    assert all(r["status"] == "passed" for r in recs), (
        f"reconciliation failures: {[r['ticker'] for r in recs if r['status'] != 'passed']}"
    )


def test_local_edges_match_reference():
    """Alignment edges: 10 Revenue_total + 10 R&D edges, all passed,
    identical to validate.py's output."""
    local = _load()
    assert local["stats"]["edges"] == 20, local["stats"]
    assert all(e["validation_status"] == "passed" for e in local["edges"])
    units_path = (ROOT / "data_ingestion_and_processing" / "data"
                  / "evidence_units" / "evidence_units.jsonl")
    from src.align.validate import load_units
    units = load_units(units_path)
    expected = []
    for concept in EDGE_CONCEPTS:
        expected.extend(build_alignment_edges(units, concept, 2025))
    assert _snapshot(local["edges"]) == _snapshot(expected)


def test_spark_mode_matches_local_mode():
    """The real Spark run must equal the local simulation. SKIPs with
    guidance when pyspark or Java (required by Spark) is unavailable."""
    try:
        import pyspark  # noqa: F401
    except ImportError:
        _skip("test_spark_mode_matches_local_mode",
              "pyspark not installed - pip install pyspark")
        return
    if not _java_available():
        _skip("test_spark_mode_matches_local_mode",
              "Java not found - install a JDK 17, e.g. "
              "`conda install -c conda-forge openjdk=17` or "
              "`winget install Microsoft.OpenJDK.17 --scope user`")
        return
    try:
        spark_result = run_spark()
    except Exception as exc:  # noqa: BLE001
        if "Java" in str(exc) or "JAVA_HOME" in str(exc):
            _skip("test_spark_mode_matches_local_mode",
                  f"Java missing for Spark: {str(exc).splitlines()[0]}")
            return
        raise
    local = _load()
    for key in ("mappings", "rejected", "validation", "edges"):
        assert _snapshot(spark_result[key]) == _snapshot(local[key]), (
            f"Spark {key} diverges from the local reference"
        )
    assert spark_result["stats"] == local["stats"]


def _run_all() -> int:
    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            if fn.__name__ not in SKIPPED:
                print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed - len(SKIPPED)}/{len(tests)} tests passed "
          f"({len(SKIPPED)} skipped)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
