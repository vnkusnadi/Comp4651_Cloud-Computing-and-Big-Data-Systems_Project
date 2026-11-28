# SectorView (COMP4651 Group 16)

Compares the five biggest US IT companies (Apple, Microsoft, Nvidia, Broadcom, Cisco) on
**scale**, **profitability** and **operating efficiency**, using their 2025 annual reports (10-K).
Code pulls and checks every number first. The AI only explains numbers that are already checked,
and every number in an answer links back to its page, table and row in the report.

> Status: work in progress. See "What still needs doing" below.

## How the pieces fit

| Stage | Folder | Owner | What it does |
|---|---|---|---|
| 1-3 Get the data | `data_ingestion_and_processing/` | Donald (A) | Downloads 10-Ks and SEC data, saves every number with its source. Airflow on Docker, one task per company and year. |
| 4-5 Check and group | `configs/sectors/`, `src/mapping/`, `src/align/`, `src/spark/` | Kelvin (B) | Dictionary of terms, matching rules, the division check, cross-company alignment. `src/spark/` runs the same steps as a Spark map-reduce job. |
| 6-7 Find and answer | `src/index/`, `src/serve/` | Faustina (C) | Reads the question, finds the numbers, does the math in code, API (`/query`, `/category`) and web page. |
| 5b-5c Fact sheet and AI verdict | `src/analysis/` | Vicko (D) | Builds the fact sheet, asks Claude for a verdict, runs 7 checks before anything is shown. |
| Evaluation | `evaluation/` | Vicko (D) | Test questions, hand-made answer key, scoring, baselines, switch-off tests, speed tests. |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                                    # all tests (Spark test needs Java 17+)

# answer a category question (code-only without a key)
python -m src.analysis.category "Is Nvidia or Cisco more profitable?"

# with the AI verdict: set a Claude API key; claude-haiku-5-5 is the cheapest model
export ANTHROPIC_API_KEY=sk-ant-...
export SECTORVIEW_MODEL=claude-haiku-5-5

# API and web page
uvicorn src.serve.api:app --port 8000
streamlit run src/serve/ui.py

# Spark job for stages 4-5 (add --copies 30 to test with 30x the data)
python -m src.spark.stage45_spark --master "local[*]"

# Data pipeline for more years (needs internet access to sec.gov)
export SEC_USER_AGENT="Your Name your.email@example.com"
cd data_ingestion_and_processing && python pipeline.py --years 2023 2024 2025
# or with Airflow:  SECTORVIEW_FISCAL_YEARS=2023,2024,2025 docker compose up
```

Evaluation steps are in [`evaluation/README.md`](evaluation/README.md).

## Known limitations

- The answer key's numbers were read from the 10-K PDFs and checked against our data, not fully by hand; its verdicts use the same rule as the code.
- The plain AI-search baseline (`evaluation/flat_rag.py`) was written but not run.
- Cadence's FY2020 report is missing (its year ended 2 Jan 2021), and IBM's numbers have no page numbers (its statements are in an exhibit).

## Person C — retrieval, generation, UI (Stage 6–7)

```
pip install -r requirements.txt
uvicorn src.serve.api:app --port 8000        # POST /query, GET /evidence/{id}, GET /health
streamlit run src/serve/ui.py                # 3 panes: conclusion | evidence | alignment diagnostics
py -m pytest tests/test_query_api.py -v
```

- `src/index/bm25.py`, `dense.py` — BM25 + dense (TF‑IDF by default; `SECTORVIEW_DENSE=sbert` for sentence‑transformers), unit text enriched with schema aliases.
- `src/serve/query_parser.py` — schema‑driven parse into sector / companies / concepts / period / intent.
- `src/serve/engine.py` — metadata‑filtered hybrid retrieval (`has_dimension=false` for consolidated concepts, `validation_status=passed`), expansion via Person B's alignment edges (`data/alignment_edges*.jsonl`, else computed with `build_alignment_edges`) and `linked_footnote_ids`, formula‑backed derived metrics, claim–evidence matrix.
- Text is generated only after evidence selection; every claim carries `evidence_unit_ids`; missing data yields an `insufficient_coverage` claim, never a guessed number.
