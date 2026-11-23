"""
Person C — retrieval / generation tests (run from repo root: py -m pytest tests/test_query_api.py).

Asserts the R&D-intensity deliverable: correct values, both companies' evidence on
every comparison claim, the has_dimension=false filter, and the FastAPI contract.
"""

from __future__ import annotations

import pytest

from src.index.bm25 import BM25Index, tokenize
from src.serve.engine import Engine

AAPL_RND, AAPL_REV = 34_550_000_000, 416_161_000_000
MSFT_RND, MSFT_REV = 32_488_000_000, 281_724_000_000
Q = "Which has higher R&D intensity, Apple or Microsoft?"


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


@pytest.fixture(scope="module")
def resp(engine):
    return engine.answer(Q)


def test_parser(engine):
    p = engine.parser.parse(Q)
    assert p.companies == ["AAPL", "MSFT"]
    assert p.metrics == ["R&D_expense_to_revenue"]
    assert p.intent == "metric_comparison"
    assert engine.parser.parse("R&D intensity of Apple in FY2024").fiscal_year == 2024
    assert engine.parser.parse("Which is more profitable?").category == "Profitability"
    assert engine.parser.parse("what is the weather").intent == "unsupported"


def test_tokenizer_handles_rnd_and_camelcase():
    assert tokenize("R&D") == ["r", "and", "d"]
    assert "research" in tokenize("ResearchAndDevelopmentExpense")


def test_values_match_gold(resp):
    by_t = {c["ticker"]: c for c in resp["claims"] if c["kind"] == "derived"}
    assert by_t["AAPL"]["value"] == pytest.approx(AAPL_RND / AAPL_REV)
    assert by_t["MSFT"]["value"] == pytest.approx(MSFT_RND / MSFT_REV)
    assert by_t["AAPL"]["formula_id"] == "f_rnd_intensity"
    assert "Microsoft" in resp["conclusion"] and "11.53%" in resp["conclusion"]


def test_every_claim_cites_existing_evidence(resp):
    ids = {e["unit_id"] for e in resp["evidence"]}
    for c in resp["claims"]:
        assert c["evidence_unit_ids"], c
        assert set(c["evidence_unit_ids"]) <= ids


def test_comparison_claim_has_evidence_from_both_companies(resp, engine):
    cmp_ = next(c for c in resp["claims"] if c["kind"] == "comparison")
    tickers = {engine.by_id[i]["ticker"] for i in cmp_["evidence_unit_ids"]}
    assert tickers == {"AAPL", "MSFT"}


def test_only_consolidated_validated_units_are_cited(resp):
    for e in resp["evidence"]:
        if e["role"] == "cited":
            assert not e["has_dimension"]
            assert e["validation_status"] == "passed"


def test_segment_rows_never_selected_as_total(engine):
    r = engine.answer("Compare total revenue of Apple and Microsoft")
    vals = {c["ticker"]: c["value"] for c in r["claims"] if c["kind"] == "fact"}
    assert vals == {"AAPL": AAPL_REV, "MSFT": MSFT_REV}
    assert any(b["reason"] == "dimension_mismatch" for b in r["blocked_candidates"])


def test_alignment_diagnostics_present(resp):
    assert {a["concept_canonical"] for a in resp["alignment"]} == {"R&D_expense", "Revenue_total"}
    assert all(a["validation_status"] == "passed" for a in resp["alignment"])


def test_missing_period_reports_insufficient_coverage_not_a_number(engine):
    r = engine.answer("R&D intensity of Apple and Microsoft in FY2019")
    assert all(c["kind"] == "coverage" for c in r["claims"])
    assert all("value" not in c for c in r["claims"])


def test_unsupported_query_is_refused(engine):
    r = engine.answer("what is the weather in Hong Kong")
    assert r["claims"] == [] and "did not answer" in r["conclusion"]


def test_index_stats(engine):
    s = engine.index_stats()
    assert s["num_units"] == len(engine.units) > 0
    assert s["bm25"]["build_time_s"] >= 0 and s["dense"]["index_size_bytes"] > 0


def test_bm25_ranks_label_match_first(engine):
    top = engine.bm25.search("research and development", k=5)
    assert top and all("research" in engine.texts[i].lower() for i, _ in top)


def test_fastapi_contract():
    from fastapi.testclient import TestClient
    from src.serve.api import app

    c = TestClient(app)
    assert c.get("/health").json()["num_units"] > 0
    r = c.post("/query", json={"query": Q})
    assert r.status_code == 200
    body = r.json()
    assert body["claims"] and all(cl["evidence_unit_ids"] for cl in body["claims"])
    uid = body["claims"][0]["evidence_unit_ids"][0]
    assert c.get(f"/evidence/{uid}").json()["unit_id"] == uid
    assert c.get("/evidence/nope").status_code == 404
