"""Person D: fixes to Person C's engine and parser, and the /category endpoint."""

import pytest
from fastapi.testclient import TestClient

from src.serve.api import app
from src.serve.engine import Engine


@pytest.fixture(scope="module")
def engine():
    return Engine()


def _derived(resp, ticker, metric):
    return next(c for c in resp["claims"] if c.get("ticker") == ticker and c.get("metric") == metric)


def test_free_cash_flow_subtracts_capex(engine):
    r = engine.answer("Compare free cash flow margin and operating cash flow margin of Broadcom and Cisco")
    fcf = _derived(r, "AVGO", "FreeCashFlowMargin")["value"]
    ocf = _derived(r, "AVGO", "OperatingCashFlow_to_revenue")["value"]
    assert fcf < ocf
    assert "Capex" in _derived(r, "AVGO", "FreeCashFlowMargin")["inputs"]


def test_free_cash_flow_missing_capex_is_reported(engine):
    r = engine.answer("Compare free cash flow margin of Nvidia and Cisco")
    nv = [c for c in r["claims"] if c.get("ticker") == "NVDA"]
    assert nv and nv[0]["kind"] == "coverage"


def test_ebitda_now_supported(engine):
    r = engine.answer("Compare EBITDA of Apple and Cisco")
    assert _derived(r, "AAPL", "EBITDA")["value"] > 1e11


@pytest.mark.parametrize("q,cat", [
    ("Is Apple or Microsoft bigger?", "Scale"),
    ("Which makes more money for each dollar of sales, Apple or Microsoft?", "Profitability"),
    ("Does Nvidia earn better margins than Broadcom?", "Profitability"),
])
def test_everyday_wording(engine, q, cat):
    p = engine.parser.parse(q)
    assert p.intent == "category_analysis" and p.category == cat


def test_specific_metric_questions_unchanged(engine):
    p = engine.parser.parse("Which of Apple and Microsoft has the larger total revenue?")
    assert p.intent == "metric_comparison" and p.metrics == ["Revenue_total"]


def test_category_endpoint():
    c = TestClient(app)
    r = c.post("/category", json={"query": "Is Nvidia or Cisco more profitable?"}).json()
    assert r["verdict"]["winner"] == "NVDA" and r["gate"]["passed"]


def test_profitability_scored_on_ratios(engine):
    from src.analysis.packet import build_packet
    p = build_packet(engine, "Profitability", ["AAPL", "MSFT", "NVDA", "AVGO", "CSCO"])
    assert "GrossProfit" not in p["common_metrics"]
    assert p["code_verdict"] == {"type": "dominant", "winner": "NVDA"}
