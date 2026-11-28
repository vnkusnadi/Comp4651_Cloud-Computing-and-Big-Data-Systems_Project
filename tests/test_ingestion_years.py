"""Person D: Person A's pipeline can ingest several fiscal years in one run
(needed for the speed test with 15+ reports). The SEC download is replaced by a
fake so this runs offline."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_ingestion_and_processing"))
pipeline = pytest.importorskip("pipeline")


class _U:
    def __init__(self, t, fy):
        self.d = {"unit_id": f"{t}{fy}", "ticker": t, "fiscal_year": fy, "metric": "Revenue_total",
                  "value": 1.0, "dimensions": {}}

    def to_dict(self):
        return dict(self.d)


def test_run_ingestion_many_years(tmp_path, monkeypatch):
    calls = []

    def fake(ticker, fiscal_year):
        calls.append((ticker, fiscal_year))
        return [_U(ticker, fiscal_year)]

    monkeypatch.setattr(pipeline, "build_evidence_units_for_company", fake)
    pipeline.run_ingestion(tickers=["AAPL", "MSFT"], output_dir=str(tmp_path), fiscal_years=[2023, 2024, 2025])
    assert len(calls) == 6
    rows = [json.loads(l) for l in (tmp_path / "evidence_units.jsonl").read_text().splitlines()]
    assert sorted({r["fiscal_year"] for r in rows}) == [2023, 2024, 2025]
    timings = json.loads((tmp_path / "ingest_timings.json").read_text())
    assert len(timings) == 6 and all("seconds" in t for t in timings)


def test_user_agent_from_environment():
    assert "User-Agent" in pipeline.SESSION.headers
