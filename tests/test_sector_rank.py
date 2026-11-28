"""Many-company growth ranking (code only), on the 30-company evidence units."""
import pytest

from src.analysis.sector_rank import IT30_UNITS, rank_growth

pytestmark = pytest.mark.skipif(not IT30_UNITS.exists(), reason="30-company evidence units not built")


@pytest.fixture(scope="module")
def result():
    from src.serve.engine import Engine
    return rank_growth(Engine(units_path=IT30_UNITS), "Revenue_total", 2021, 2025)


def test_ranks_many_companies_with_sources(result):
    assert len(result["ranked"]) >= 25
    growth = [r["growth"] for r in result["ranked"]]
    assert growth == sorted(growth, reverse=True)
    for r in result["ranked"]:
        assert r["start"]["fiscal_year"] == 2021 and r["end"]["fiscal_year"] == 2025
        assert r["start"]["accession_number"] != r["end"]["accession_number"]   # two different reports
        assert r["start"]["unit_id"] and r["end"]["unit_id"]


def test_known_values(result):
    by = {r["ticker"]: r for r in result["ranked"]}
    a = by["AAPL"]
    assert a["start"]["value"] == 365_817e6 and a["end"]["value"] == 416_161e6
    assert by["NVDA"]["rank"] < by["AAPL"]["rank"]          # Nvidia grew far faster than Apple
