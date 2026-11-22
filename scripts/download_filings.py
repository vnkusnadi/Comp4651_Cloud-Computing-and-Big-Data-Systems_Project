"""Download SEC 10-K primary documents for the IT-sector cohort.

Usage (from the repository root):
    python scripts/download_filings.py [out_dir] [--tickers AAPL,MSFT]

Reads data_ingestion_and_processing/data/filing_index.csv (ticker, cik,
accession_number, primary_document) and downloads each primary document
from EDGAR into <out_dir>/<ticker>_<primary_document>, plus a
filing_metadata.json summary.

SEC fair-access: custom User-Agent, retries with backoff.
"""
import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "data_ingestion_and_processing" / "data" / "filing_index.csv"
DEFAULT_OUT = REPO / "data_ingestion_and_processing" / "staging"
HEADERS = {
    "User-Agent": "PersonB-Research/1.0 (contact@example.com)",  # SEC fair-access policy
    # NOTE: do NOT send Accept-Encoding here — urllib does not decode gzip
    # automatically, and we want the plain HTML body.
}


def download(url: str, dest: Path, tries: int = 3) -> int:
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            dest.write_bytes(data)
            return len(data)
        except Exception as exc:  # noqa: BLE001
            print(f"    attempt {attempt}/{tries} failed: {exc}")
            if attempt < tries:
                time.sleep(2 * attempt)
    return 0


def main() -> None:
    args = sys.argv[1:]
    ticker_filter = None
    if "--tickers" in args:
        i = args.index("--tickers")
        ticker_filter = {t.strip().upper() for t in args[i + 1].split(",")}
        del args[i:i + 2]
    out_dir = Path(args[0]) if args else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(INDEX.read_text(encoding="utf-8").splitlines()))
    done = []
    for row in rows:
        ticker = row["ticker"]
        if ticker_filter and ticker not in ticker_filter:
            continue
        cik = row["cik"]
        acc = row["accession_number"].replace("-", "")
        doc = row["primary_document"]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}"
        dest = out_dir / f"{ticker}_{doc}"
        print(f"[{ticker}] {url}")
        size = download(url, dest)
        if size:
            print(f"    saved {size:,} bytes -> {dest.name}")
            done.append({**row, "local_file": dest.name, "bytes": size})
        else:
            print(f"    FAILED for {ticker}")
    if done:
        meta = out_dir / "filing_metadata.json"
        meta.write_text(json.dumps(done, indent=2), encoding="utf-8")
        print(f"\n{len(done)} filing(s) downloaded -> {out_dir} ({meta.name})")
    else:
        print("\nNo filings downloaded.")


if __name__ == "__main__":
    main()
