"""Multi-year growth answers (code only), on the 5-year evidence units."""
import pytest

from src.analysis.growth import MULTI_YEAR_UNITS, answer_growth, parse_growth

pytestmark = pytest.mark.skipif(not MULTI_YEAR_UNITS.exists(), reason="5-year evidence units not built")


@pytest.fixture(scope="module")
def engine():
    from src.serve.engine import Engine
    return Engine(units_path=MULTI_YEAR_UNITS)


def test_parse(engine):
    p = parse_growth(engine, "Which grew revenue faster from fiscal 2021 to fiscal 2025, Apple or Microsoft?")
    assert p == {"metric": "Revenue_total", "tickers": ["AAPL", "MSFT"], "fy_from": 2021, "fy_to": 2025}
    assert parse_growth(engine, "Which is bigger, Apple or Microsoft?") is None


def test_revenue_growth_uses_two_filings(engine):
    r = answer_growth("Which grew revenue faster from 2021 to 2025, Apple or Microsoft?", engine)
    assert r["winner"] == "MSFT"
    a = r["per_company"]["AAPL"]
    assert a["start"]["value"] == 365_817e6 and a["end"]["value"] == 416_161e6
    assert a["start"]["accession_number"] != a["end"]["accession_number"]
    assert abs(a["growth"] - (416_161 / 365_817 - 1)) < 1e-9
