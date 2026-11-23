"""
Person C — Stage 7: Streamlit UI (three panes).

    streamlit run src/serve/ui.py

  1. Generated conclusion + atomic claims (pick a claim to click through)
  2. Evidence table: the exact source rows behind the selected claim
  3. Alignment diagnostics: cross-company edges, retrieval filters, and the
     candidates that were blocked (e.g. segment rows / dimensional facts)

Talks to the FastAPI service (SECTORVIEW_API, default http://localhost:8000);
if it is not running it falls back to the in-process engine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

API = os.environ.get("SECTORVIEW_API", "http://localhost:8000")
EXAMPLES = [
    "Which has higher R&D intensity, Apple or Microsoft?",
    "Compare gross margin of Apple and Microsoft",
    "Which of Apple and Microsoft has the larger total revenue?",
    "Which company is more profitable?",
]

st.set_page_config(page_title="SectorView", layout="wide")


@st.cache_resource(show_spinner="Building indexes…")
def _engine():
    from src.serve.engine import Engine
    return Engine()


def ask(q: str) -> Dict[str, Any]:
    try:
        import requests
        r = requests.post(f"{API}/query", json={"query": q}, timeout=30)
        r.raise_for_status()
        return {**r.json(), "_via": "api"}
    except Exception:
        return {**_engine().answer(q), "_via": "in-process"}


def evidence_df(rows) -> pd.DataFrame:
    return pd.DataFrame([{
        "unit_id": e["unit_id"], "company": e.get("ticker"), "FY": e.get("fiscal_year"),
        "metric": e.get("metric"), "row label": e.get("row_label"),
        "value": e.get("value"), "unit": e.get("unit"),
        "section": e.get("section_path") or "—", "page": e["source"].get("page"),
        "table": e["source"].get("table_id"), "dimensions": str(e.get("dimensions") or "none"),
        "mapping": e.get("mapping_method"), "status": e.get("validation_status"),
        "role": e.get("role"), "SEC filing": e["source"].get("filing_index"),
    } for e in rows])


st.title("SectorView")
st.caption("Cross-company evidence with cell-level provenance — Information Technology (GICS 45)")

with st.sidebar:
    st.header("Question")
    pick = st.selectbox("Examples", EXAMPLES)
    q = st.text_input("Ask", value=pick)
    go = st.button("Ask", type="primary")

if go or "resp" not in st.session_state:
    st.session_state["resp"] = ask(q)
resp = st.session_state["resp"]

col1, col2, col3 = st.columns([1.1, 1.6, 1.1])

# ---- Pane 1: conclusion + claims -------------------------------------------
with col1:
    st.subheader("1 · Conclusion")
    st.info(resp["conclusion"])
    p = resp["parsed"]
    st.caption(f"intent **{p['intent']}** · companies {', '.join(p['companies'])} · "
               f"metrics {', '.join(p['metrics'][:4])}{'…' if len(p['metrics']) > 4 else ''} · "
               f"FY {p['fiscal_year'] or 'latest'}")
    claims = resp["claims"]
    labels = [f"{c['claim_id']} · {c['kind']} · {c['text'][:70]}" for c in claims]
    sel = st.radio("Claims — pick one to see its evidence", range(len(claims)),
                   format_func=lambda i: labels[i], index=0 if claims else None) if claims else None
    if sel is not None:
        c = claims[sel]
        st.markdown(f"**{c['text']}**")
        if c.get("formula"):
            st.code(f"{c.get('formula_id')}: {c['formula']}", language=None)
        st.caption(f"evidence_unit_ids: {', '.join(c['evidence_unit_ids']) or '—'}")
    st.caption(f"served via {resp['_via']} · {resp['diagnostics'].get('latency_ms')} ms")

# ---- Pane 2: evidence -------------------------------------------------------
with col2:
    st.subheader("2 · Evidence (source rows)")
    ev = resp["evidence"]
    chosen = set(claims[sel]["evidence_unit_ids"]) if sel is not None else set()
    show_all = st.toggle("Show all retrieved evidence", value=not chosen)
    rows = ev if show_all or not chosen else [e for e in ev if e["unit_id"] in chosen]
    if rows:
        st.dataframe(evidence_df(rows), hide_index=True, width="stretch",
                     column_config={"SEC filing": st.column_config.LinkColumn("SEC filing", display_text="open")})
        for e in rows:
            if e["unit_id"] in chosen:
                s = e["source"]
                with st.expander(f"{e['ticker']} · {e.get('row_label')} · {e['unit_id']}", expanded=True):
                    st.write(f"**{e.get('value'):,}** {e.get('unit')}  \n"
                             f"page {s.get('page') or 'n/a'} · table {s.get('table_id')} · "
                             f"row “{s.get('row_label')}” · col “{s.get('col_label')}”  \n"
                             f"section: {s.get('section_path') or 'n/a'}  \n"
                             f"accession {e.get('accession_number')} · filed {e.get('filing_date')}")
                    if s.get("filing_index"):
                        st.markdown(f"[Open filing on SEC EDGAR]({s['filing_index']})")
    else:
        st.write("No evidence retrieved.")

# ---- Pane 3: alignment diagnostics -----------------------------------------
with col3:
    st.subheader("3 · Alignment diagnostics")
    al = resp["alignment"]
    if al:
        st.dataframe(pd.DataFrame([{
            "concept": a.get("concept_canonical"), "A": a.get("unit_a_ticker"), "B": a.get("unit_b_ticker"),
            "status": a.get("validation_status"), "reason": a.get("validation_reason"),
            "gap": None if a.get("gap") is None else round(a["gap"], 4),
            "uses cited units": a.get("uses_selected_units"),
        } for a in al]), hide_index=True, width="stretch")
    else:
        st.caption("No alignment edges for this query.")
    with st.expander(f"Blocked candidates ({len(resp['blocked_candidates'])})"):
        st.caption("Same-concept units NOT used — segment / dimensional facts and non-validated rows.")
        if resp["blocked_candidates"]:
            st.dataframe(pd.DataFrame(resp["blocked_candidates"]), hide_index=True, width="stretch")
    with st.expander("Retrieval trace"):
        st.json(resp["diagnostics"]["retrieval"])
    if resp["diagnostics"]["notes"] or resp["diagnostics"]["unsupported"]:
        with st.expander("Notes"):
            st.write(resp["diagnostics"]["notes"])
            st.write(resp["diagnostics"]["unsupported"])
