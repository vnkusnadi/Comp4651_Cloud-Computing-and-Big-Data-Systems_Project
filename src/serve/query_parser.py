"""
Person C — Stage 7, step 1: parse a natural-language query into
(sector, companies, concepts, period, intent).

Rule-based on purpose: the parse must be deterministic and auditable, and it
is driven by Person B's sector schema (aliases, derived metrics, categories),
so adding a concept to the YAML makes it queryable with no code change.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Company name -> ticker. Tickers themselves are also recognised.
COMPANY_ALIASES = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA",
    "broadcom": "AVGO", "cisco": "CSCO",
}

# Hand-written synonyms on top of the schema aliases.
METRIC_SYNONYMS = {
    "R&D_expense_to_revenue": ["r&d intensity", "r&d to revenue", "r&d spending",
                               "r&d spend", "research intensity", "r&d as a share of revenue",
                               "research and development intensity"],
    "SalesAndMarketing_to_revenue": ["sales and marketing intensity", "s&m to revenue"],
    "G&A_to_revenue": ["g&a intensity", "g&a to revenue"],
    "TotalOperatingExpense_to_revenue": ["opex to revenue", "operating expense ratio"],
    "Capex_to_revenue": ["capex intensity", "capex to revenue", "capital intensity"],
    "OperatingCashFlow_to_revenue": ["operating cash flow margin", "ocf margin"],
    "GrossMargin": ["gross margin"],
    "OperatingMargin": ["operating margin"],
    "NetMargin": ["net margin", "profit margin"],
    "ReturnOnAssets": ["return on assets", "roa"],
    "ReturnOnEquity": ["return on equity", "roe"],
    "AssetTurnover": ["asset turnover"],
    "Revenue_total": ["total revenue", "revenue", "net sales", "sales"],
    "NetIncomeLoss": ["net income", "net profit", "earnings"],
    "OperatingIncomeLoss": ["operating income", "operating profit"],
    "Assets": ["total assets", "assets"],
}

CATEGORY_KEYWORDS = {
    "Scale": ["scale", "largest", "biggest", "size"],
    "Profitability": ["profitab", "profitable"],
    "Operating efficiency": ["operating efficiency", "operating profile", "efficien"],
}


@dataclass
class ParsedQuery:
    query: str
    sector: str
    companies: List[str]
    metrics: List[str]                 # canonical names (derived or base)
    category: Optional[str]
    fiscal_year: Optional[int]         # None -> latest available
    intent: str                        # metric_comparison | metric_lookup | category_analysis
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _kw_pattern(kw: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])")


class QueryParser:
    def __init__(self, schema: Dict[str, Any]):
        self.sector = schema["sector"]["name"]
        self.sector_tickers = list(schema["sector"].get("companies", []))
        self.categories = {c["name"]: c for c in schema.get("categories", [])}
        # keyword -> canonical metric, longest keyword first (most specific wins)
        kw: Dict[str, str] = {}
        for d in schema.get("derived_metrics", []):
            name = d["canonical_name"]
            kw[name.lower().replace("_", " ")] = name
            for s in METRIC_SYNONYMS.get(name, []):
                kw[s.lower()] = name
        for c in schema.get("base_concepts", []):
            name = c["canonical_name"]
            kw.setdefault(name.lower().replace("_", " "), name)
            for a in c.get("aliases") or []:
                kw.setdefault(str(a).lower(), name)
            for s in METRIC_SYNONYMS.get(name, []):
                kw.setdefault(s.lower(), name)
        # bare "r&d" / "research and development" -> intensity (the demo question)
        kw.setdefault("r&d", "R&D_expense_to_revenue")
        kw["research and development"] = "R&D_expense_to_revenue"
        self.keywords = sorted(kw.items(), key=lambda kv: -len(kv[0]))

    def parse(self, query: str) -> ParsedQuery:
        q = query.lower()
        notes: List[str] = []

        companies: List[str] = []
        for name, tk in COMPANY_ALIASES.items():
            if _kw_pattern(name).search(q) and tk not in companies:
                companies.append(tk)
        for tk in self.sector_tickers:
            if _kw_pattern(tk.lower()).search(q) and tk not in companies:
                companies.append(tk)
        if not companies:
            companies = list(self.sector_tickers)
            notes.append("no company named: using the whole sector peer set")

        m = re.search(r"(?:fy\s?|fiscal\s+(?:year\s+)?)(20\d{2})", q) or re.search(r"\b(20\d{2})\b", q)
        fiscal_year = int(m.group(1)) if m else None

        # Specific metrics: longest keyword first, consume matched span so
        # "operating margin" is not also read as "operating income".
        metrics: List[str] = []
        remaining = q
        for kw_text, name in self.keywords:
            pat = _kw_pattern(kw_text)
            if pat.search(remaining):
                if name not in metrics:
                    metrics.append(name)
                remaining = pat.sub(" ", remaining)

        category: Optional[str] = None
        if not metrics:
            for cat, kws in CATEGORY_KEYWORDS.items():
                if any(k in q for k in kws):
                    category = cat
                    break
            if category:
                metrics = [x for x in self.categories[category]["members"]]
                notes.append(f"category '{category}' expanded to its {len(metrics)} member metrics")

        if category:
            intent = "category_analysis"
        elif not metrics:
            intent = "unsupported"
            notes.append("no schema concept recognised in the query")
        else:
            intent = "metric_comparison" if len(companies) > 1 else "metric_lookup"

        return ParsedQuery(query=query, sector=self.sector, companies=companies,
                           metrics=metrics, category=category,
                           fiscal_year=fiscal_year, intent=intent, notes=notes)
