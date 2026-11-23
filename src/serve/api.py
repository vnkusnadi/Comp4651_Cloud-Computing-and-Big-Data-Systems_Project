"""
Person C — Stage 7: FastAPI service.

    uvicorn src.serve.api:app --reload --port 8000

POST /query            -> conclusion + structured claims (each with evidence_unit_ids),
                          evidence rows with source pointers, alignment diagnostics
GET  /evidence/{id}    -> one evidence unit (click-through target for the UI)
GET  /health           -> index stats (build_time, index_size, num_units)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.serve.engine import Engine, edgar_urls, has_dimension

app = FastAPI(title="SectorView query API", version="0.1")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return Engine()


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, examples=["Which has higher R&D intensity, Apple or Microsoft?"])


class Claim(BaseModel):
    claim_id: str
    kind: str                                  # fact | derived | comparison | coverage
    text: str
    evidence_unit_ids: List[str]
    metric: Optional[str] = None
    ticker: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    formula_id: Optional[str] = None
    formula: Optional[str] = None
    inputs: Optional[Dict[str, str]] = None
    leader: Optional[str] = None
    ranking: Optional[List[str]] = None
    companies: Optional[List[str]] = None
    status: Optional[str] = None


class QueryResponse(BaseModel):
    query: str
    parsed: Dict[str, Any]
    conclusion: str
    claims: List[Claim]
    evidence: List[Dict[str, Any]]
    alignment: List[Dict[str, Any]]
    blocked_candidates: List[Dict[str, Any]]
    diagnostics: Dict[str, Any]


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", **get_engine().index_stats()}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> Dict[str, Any]:
    return get_engine().answer(req.query)


@app.get("/evidence/{unit_id}")
def evidence(unit_id: str) -> Dict[str, Any]:
    u = get_engine().by_id.get(unit_id)
    if u is None:
        raise HTTPException(status_code=404, detail=f"unknown unit_id {unit_id}")
    return {**u, "has_dimension": has_dimension(u), "source": edgar_urls(u)}
