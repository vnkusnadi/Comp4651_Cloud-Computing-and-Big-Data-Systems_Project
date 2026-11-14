import pandas as pd
from pipeline import find_filing_for_year, CIK_MAP

df = pd.read_parquet("data/evidence_units/evidence_units.parquet")
idx = df.drop_duplicates("ticker")[["ticker", "cik", "accession_number"]].copy()
idx["primary_document"] = idx.ticker.apply(
    lambda t: find_filing_for_year(CIK_MAP[t], 2025)["primary_document"]
)
idx.to_csv("data/filing_index.csv", index=False)
