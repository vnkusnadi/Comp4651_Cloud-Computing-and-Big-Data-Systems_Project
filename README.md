# Comp4651_Cloud-Computing-and-Big-Data-Systems_Project
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
