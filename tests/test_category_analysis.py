"""Person D: tests for the fact sheet (Stage 5b), the 7-check gate and AI
verdict flow (Stage 5c), and the evaluation scoring. No network, no API key:
the AI is replaced by small fake functions."""

import copy
import csv
import json

import pytest

from src.analysis.gate import extract_numbers, run_gate
from src.analysis.packet import build_packet
from src.util.formula import eval_formula
from src.analysis.synthesize import code_only_verdict, judge
from src.analysis import category as cat
from src.serve.engine import Engine


@pytest.fixture(scope="module")
def engine():
    return Engine()


# ------------------------------------------------------------------ formulas
def test_formula_handles_minus_and_abs():
    v = eval_formula("(OperatingCashFlow - abs(Capex)) / Revenue_total",
                     {"OperatingCashFlow": 50.0, "Capex": -10.0, "Revenue_total": 100.0})
    assert v == pytest.approx(0.40)


def test_formula_rejects_code():
    with pytest.raises(ValueError):
        eval_formula("__import__('os').system('x')", {})


# ------------------------------------------------------------------ packet
def test_free_cash_flow_subtracts_capex(engine):
    p = build_packet(engine, "Operating efficiency", ["AVGO", "CSCO"])
    fcf = p["metrics"]["FreeCashFlowMargin"]["values"]["AVGO"]["value"]
    ocf = p["metrics"]["OperatingCashFlow_to_revenue"]["values"]["AVGO"]["value"]
    capex = p["metrics"]["Capex_to_revenue"]["values"]["AVGO"]["value"]
    assert fcf == pytest.approx(ocf - capex)
    assert fcf < ocf


def test_missing_capex_means_missing_fcf_not_a_guess(engine):
    p = build_packet(engine, "Operating efficiency", ["NVDA", "AVGO"])
    assert "NVDA" not in p["metrics"]["FreeCashFlowMargin"]["values"]
    assert any(m["ticker"] == "NVDA" and m["metric"] == "FreeCashFlowMargin" for m in p["missing"])


def test_nvidia_dominates_cisco_on_profitability(engine):
    p = build_packet(engine, "Profitability", ["NVDA", "CSCO"])
    assert p["pareto"]["dominant_company"] == "NVDA"
    assert p["code_verdict"] == {"type": "dominant", "winner": "NVDA"}
    assert p["stability"]["label"] == "stable"


def test_apple_vs_microsoft_scale_is_a_tradeoff(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    assert p["pareto"]["dominant_company"] is None
    assert p["metrics"]["Revenue_total"]["leader"] == "AAPL"
    assert p["metrics"]["Assets"]["leader"] == "MSFT"
    assert p["code_verdict"]["type"] == "tradeoff"
    assert p["stability"]["label"] in ("mixed", "fragile", "stable")


def test_total_revenue_never_a_division_row(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT", "NVDA", "AVGO", "CSCO"])
    for t, v in p["metrics"]["Revenue_total"]["values"].items():
        u = engine.by_id[v["unit_ids"][0]]
        assert not u.get("dimensions"), t
    assert p["metrics"]["Revenue_total"]["values"]["AAPL"]["value"] == 416161000000.0


def test_efficiency_is_a_profile_without_winner(engine):
    p = build_packet(engine, "Operating efficiency", ["NVDA", "AVGO"])
    assert p["code_verdict"]["type"] == "profile" and p["code_verdict"]["winner"] is None
    assert any(e["metric"] == "SalesAndMarketing_to_revenue" for e in p["estimates"])


# ------------------------------------------------------------------ gate
def test_code_only_verdict_passes_all_7_checks(engine):
    for category, tickers in [("Scale", ["AAPL", "MSFT"]), ("Profitability", ["NVDA", "CSCO"]),
                              ("Operating efficiency", ["NVDA", "AVGO"]),
                              ("Profitability", ["AAPL", "MSFT", "NVDA", "AVGO", "CSCO"])]:
        p = build_packet(engine, category, tickers)
        g = run_gate(code_only_verdict(p), p)
        assert g["passed"], (category, tickers, g["failed"])
        assert len(g["checks"]) == 7


def _good_ai(packet):
    v = code_only_verdict(packet)
    v["summary"] = ("Apple is bigger overall because its revenue is $416.2B against "
                    "Microsoft's $281.7B, but Microsoft owns more assets.")
    return v


def test_gate_catches_made_up_number(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    v = _good_ai(p)
    assert run_gate(v, p)["passed"]
    v["summary"] = "Apple's revenue is $450.0B."
    g = run_gate(v, p)
    assert "numbers_in_packet" in g["failed"]


def test_gate_catches_wrong_leader(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    v = _good_ai(p)
    v["tradeoffs"][0] = {**v["tradeoffs"][0], "metric": "Assets", "leader": "AAPL"}
    assert "leaders_match_code" in run_gate(v, p)["failed"]


def test_gate_catches_ignoring_pareto(engine):
    p = build_packet(engine, "Profitability", ["NVDA", "CSCO"])
    v = code_only_verdict(p)
    v["winner"] = "CSCO"
    assert "verdict_fits_pareto" in run_gate(v, p)["failed"]


def test_gate_catches_hidden_missing_data(engine):
    p = build_packet(engine, "Operating efficiency", ["NVDA", "AVGO"])
    v = code_only_verdict(p)
    v["missing_disclosure"] = "Nothing is missing."
    assert "coverage_disclosed" in run_gate(v, p)["failed"]


def test_gate_catches_claim_without_both_sources(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    v = code_only_verdict(p)
    c = v["claims"][0]
    c["unit_ids"] = p["metrics"][c["metric"]]["values"]["AAPL"]["unit_ids"]
    assert "claims_cite_both" in run_gate(v, p)["failed"]


def test_gate_catches_wrong_stability(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    v = code_only_verdict(p)
    v["stability_label"] = "stable" if p["stability"]["label"] != "stable" else "fragile"
    assert "stability_disclosed" in run_gate(v, p)["failed"]


def test_number_parser():
    assert extract_numbers("scaled 0-1, revenue $416.2B (-12.5%) in FY2025, 3 of 5") == [416.2, -12.5]


# ------------------------------------------------------------------ AI flow (fake AI)
def test_ai_verdict_is_shown_only_after_gate(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    r = judge("q", p, llm=lambda system, messages: _good_ai(p))
    assert r["status"] == "ai_verified" and r["gate"]["passed"]


def test_bad_ai_retries_then_falls_back_to_code(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    calls = []

    def bad(system, messages):
        calls.append(messages[-1]["content"])
        v = _good_ai(p)
        v["summary"] = "Apple's revenue is $999.0B."
        return v

    r = judge("q", p, llm=bad)
    assert r["status"] == "code_only_fallback"
    assert len(calls) == 2 and "failed these automatic checks" in calls[1]
    assert r["gate"]["passed"]                        # what is shown passes the gate


def test_ai_fixes_answer_on_retry(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])
    n = {"i": 0}

    def flaky(system, messages):
        n["i"] += 1
        v = _good_ai(p)
        if n["i"] == 1:
            v["summary"] = "Apple's revenue is $999.0B."
        return v

    assert judge("q", p, llm=flaky)["status"] == "ai_verified"


def test_ai_error_falls_back(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])

    def boom(system, messages):
        raise RuntimeError("network down")

    assert judge("q", p, llm=boom)["status"] == "code_only_fallback"


def test_gate_off_switch_for_ablation(engine):
    p = build_packet(engine, "Scale", ["AAPL", "MSFT"])

    def bad(system, messages):
        v = _good_ai(p)
        v["summary"] = "Apple's revenue is $999.0B."
        return v

    r = judge("q", p, llm=bad, use_gate=False)
    assert r["status"] == "ai_unchecked" and not r["gate"]["passed"]


def test_category_answer_end_to_end_without_key(engine):
    r = cat.answer("Is Nvidia or Cisco more profitable?", engine=engine, llm=None)
    assert r["status"] == "code_only_no_llm"
    assert r["verdict"]["winner"] == "NVDA"
    other = cat.answer("Compare R&D intensity of NVDA and AVGO", engine=engine, llm=None)
    assert other["analysis"]["status"] == "not_a_category_question"


# ------------------------------------------------------------------ scoring
def test_scoring_uses_hand_values(tmp_path, monkeypatch, engine):
    from evaluation import score

    vals = tmp_path / "answer_key_values.csv"
    with vals.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "metric", "priority", "look_for_row", "value_in_10k", "unit",
                    "page_in_10k", "row_label_in_10k", "notes", "filled_by"])
        w.writerow(["AAPL", "Revenue_total", "required", "", "416,161", "USD millions", "31", "Total net sales", "", "D"])
        w.writerow(["MSFT", "Revenue_total", "required", "", "281,724", "USD millions", "50", "Total revenue", "", "D"])
    ver = tmp_path / "answer_key_verdicts.csv"
    with ver.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question_id", "expected_verdict_type", "acceptable_winners", "your_reasoning", "filled_by"])
        w.writerow(["Q01", "tradeoff", "AAPL", "", "D"])
    monkeypatch.setattr(score, "HERE", tmp_path)
    values, verdicts = score.load_key()
    assert values[("AAPL", "Revenue_total")]["value"] == 416161e6
    res = tmp_path / "r.jsonl"
    good = {"question_id": "Q01", "system": "t", "winner": "AAPL", "verdict_type": "tradeoff",
            "numbers": [{"ticker": "AAPL", "metric": "Revenue_total", "value": 416161e6, "page": 31},
                        {"ticker": "MSFT", "metric": "Revenue_total", "value": 245122e6, "page": 50}],
            "claims": [], "latency_ms": 5}
    res.write_text(json.dumps(good) + "\n")
    from src.util.yaml import load as load_yaml
    s = score.score_file(res, values, verdicts, load_yaml(score.SCHEMA))
    assert s["answer_accuracy_%"] == 100.0
    assert s["number_accuracy_%"] == 50.0           # Microsoft number is wrong
    assert s["strict_accuracy_%"] == 0.0            # right answer, wrong number -> fails strict
