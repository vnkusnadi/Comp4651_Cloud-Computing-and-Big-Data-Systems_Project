"""
Person A deliverable: ingestion + evidence-unit construction.
Sector: Information Technology (GICS 45)
Companies: AAPL, MSFT, NVDA, TSMC, ASML, AVGO, CSCO
Fiscal year: FY2024 (10-K / 20-F)
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from lxml import etree
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# 0. Configuration
# ---------------------------------------------------------------------------

SEC_USER_AGENT = "PersonA-Research/1.0 (contact@example.com)"   # SEC fair-access policy

CIK_MAP = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "AVGO": "0001730168",
    "CSCO": "0000858877",
}

TICKERS = list(CIK_MAP.keys())
TARGET_FISCAL_YEAR = 2025

# Filing form types: US domestic filers use 10-K
FORM_TYPES = {"10-K"}

# ---------------------------------------------------------------------------
# 1. XBRL tag taxonomy (US GAAP + IFRS fallbacks)
# ---------------------------------------------------------------------------

# The mapping is intentionally redundant: for each metric we list candidate tags
# in priority order.  The resolver picks the first tag that has a consolidated
# fact for the requested period.
METRIC_TAG_MAP: Dict[str, List[str]] = {
    # --- Growth & scale -----------------------------------------------------
    "Revenue_total": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "Revenue",  # IFRS
    ],
    "Revenue_product": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        # Product revenue is usually dimensional.  The resolver will look for a
        # fact carrying the ProductOrServiceAxis dimension and a member whose
        # label contains "Product".
    ],
    "Revenue_service": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        # Service revenue is usually dimensional (ServiceMember).
    ],
    "CostOfRevenue": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ],
    "GrossProfit": ["GrossProfit"],
    "OperatingIncomeLoss": ["OperatingIncomeLoss"],
    "NetIncomeLoss": ["NetIncomeLoss", "ProfitLoss"],  # ProfitLoss for IFRS
    "EarningsPerShareDiluted": [
        "EarningsPerShareDiluted",
        "IncomeLossFromContinuingOperationsPerDilutedShare",
    ],
    "EarningsPerShareBasic": [
        "EarningsPerShareBasic",
        "IncomeLossFromContinuingOperationsPerBasicShare",
    ],
    "WeightedAverageNumberOfDilutedSharesOutstanding": [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "WeightedAverageNumberOfSharesOutstandingBasic": [
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    "ContractWithCustomerLiabilityCurrent": [   # deferred revenue (current)
        "ContractWithCustomerLiabilityCurrent",
        "DeferredRevenueCurrent",
    ],
    "AccountsReceivableNetCurrent": [
        "AccountsReceivableNetCurrent",
        "AccountsNotesAndLoansReceivableNetCurrent",
        "TradeAndOtherReceivablesCurrent",  # IFRS
    ],
    "InventoryNet": ["InventoryNet", "InventoriesNetOfWriteDown"],  # IFRS fallback
    # --- Profitability ------------------------------------------------------
    "Assets": ["Assets"],
    "StockholdersEquity": ["StockholdersEquity", "Equity"],  # Equity for IFRS
    # --- Operating efficiency ----------------------------------------------
    "ResearchAndDevelopmentExpense": [
        "ResearchAndDevelopmentExpense",
    ],
    "SellingAndMarketingExpense": [
        "SellingAndMarketingExpense",
        "MarketingExpense",
        "SellingGeneralAndAdministrativeExpense",  # if split unavailable
    ],
    "GeneralAndAdministrativeExpense": [
        "GeneralAndAdministrativeExpense",
    ],
    "OperatingExpenses": ["OperatingExpenses", "CostsAndExpenses"],
    "PaymentsToAcquirePropertyPlantAndEquipment": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",  # IFRS
    ],
    "NetCashProvidedByUsedInOperatingActivities": [
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",  # IFRS
    ],
    "DepreciationDepletionAndAmortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortisationExpense",  # IFRS
    ],
    "ShareBasedCompensation": ["ShareBasedCompensation"],
}

# Metrics that must be consolidated (no segment dimension).
CONSOLIDATED_METRICS = {
    "Revenue_total",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "ResearchAndDevelopmentExpense",
    "OperatingExpenses",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "DepreciationDepletionAndAmortization",
    "Assets",
    "StockholdersEquity",
}

# ---------------------------------------------------------------------------
# 2. HTTP session
# ---------------------------------------------------------------------------

def build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update({
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    })
    return session

SESSION = build_session()

# ---------------------------------------------------------------------------
# 3. SEC EDGAR helpers
# ---------------------------------------------------------------------------

def fetch_companyfacts(cik: str) -> Dict[str, Any]:
    """Download the full companyfacts JSON for a CIK."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_submissions(cik: str) -> Dict[str, Any]:
    """Download the submissions index (to locate the FY2024 10-K / 20-F accession)."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def find_filing_for_year(cik: str, fiscal_year: int = TARGET_FISCAL_YEAR) -> Optional[Dict[str, Any]]:
    """
    Return metadata for the 10-K whose fiscal period ends in `fiscal_year`.
    `reportDate` from submissions.json is the fiscal-period end, so the
    year of `reportDate` IS the fiscal year.
    """
    subs = fetch_submissions(cik)
    recent = subs.get("filings", {}).get("recent", {})

    for form, acc, doc, fd, rd in zip(
        recent.get("form", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
        recent.get("filingDate", []),
        recent.get("reportDate", []),
    ):
        if form not in FORM_TYPES or not rd or not fd:
            continue
        if int(rd[:4]) != fiscal_year:
            continue
        return {
            "accession_number": acc,
            "primary_document": doc,
            "form": form,
            "filing_date": fd,
            "report_date": rd,               # e.g. "2025-09-27" for AAPL
            "fiscal_year": fiscal_year,      # 2025
        }
    return None


def download_filing_document(cik: str, accession_number: str, primary_document: str) -> bytes:
    """Download the primary document (HTML or PDF) for a filing."""
    acc_no_dashes = accession_number.replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{acc_no_dashes}/{primary_document}"
    )
    resp = SESSION.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content

# ---------------------------------------------------------------------------
# 4. XBRL fact extraction and resolution
# ---------------------------------------------------------------------------

@dataclass
class XbrlFact:
    """A single numeric fact extracted from companyfacts."""
    tag: str
    value: float
    unit: str
    fiscal_year: int
    fiscal_period: str          # "FY"
    form: str                   # "10-K" / "20-F"
    filed: str                  # filing date
    frame: str                  # e.g. "CY2024"
    dimensions: Dict[str, str]  # axis -> member (empty for consolidated facts)
    accession_number: str
    source: str = "companyfacts"


def extract_facts_for_tag(
    companyfacts: Dict[str, Any],
    tag: str,
    period_end: str,                        # ← e.g. "2024-09-28"
    form_types: set = FORM_TYPES,
) -> List[XbrlFact]:
    """
    Pull facts for `tag` whose period end EXACTLY matches `period_end`.
    This pins to one fiscal year's consolidated totals and excludes
    the 2–3 comparative years bundled into every 10-K.
    """
    results: List[XbrlFact] = []
    for taxonomy in ("us-gaap", "ifrs-full", "dei"):
        concepts = companyfacts.get("facts", {}).get(taxonomy, {})
        if tag not in concepts:
            continue
        for unit_name, facts in concepts[tag].get("units", {}).items():
            for f in facts:
                if f.get("end") != period_end:          # ← the fix
                    continue
                if f.get("form", "") not in form_types:
                    continue
                if f.get("fp") not in ("FY", ""):
                    continue
                val = f.get("val")
                if val is None:
                    continue
                results.append(XbrlFact(
                    tag=tag, value=float(val), unit=unit_name,
                    fiscal_year=int(period_end[:4]),
                    fiscal_period="FY",
                    form=f["form"], filed=f.get("filed", ""),
                    frame=f.get("frame", ""), dimensions={},
                    accession_number=f.get("accn", ""),
                ))
    results.sort(key=lambda x: x.filed)
    return results


DIMENSIONAL_METRICS = {"Revenue_product", "Revenue_service", "Revenue_segment"}

def resolve_metric(
    companyfacts: Dict[str, Any],
    metric: str,
    period_end: str,
    target_accn: str,
) -> Optional[XbrlFact]:
    if metric in DIMENSIONAL_METRICS:
        return None
    for tag in METRIC_TAG_MAP.get(metric, []):
        facts = extract_facts_for_tag(companyfacts, tag, period_end=period_end)
        if not facts:
            continue
        if metric in CONSOLIDATED_METRICS:
            facts = [f for f in facts if not f.dimensions]
            if not facts:
                continue
        # Prefer the fact reported in the target filing
        target_facts = [f for f in facts if f.accession_number == target_accn]
        if target_facts:
            return target_facts[0]
        # Fall back to most recent — happens only if this metric wasn't in
        # the target filing (restatement, tag change, or truly absent)
        facts.sort(key=lambda x: x.filed, reverse=True)
        return facts[0]
    return None


def resolve_all_metrics(
    companyfacts: Dict[str, Any],
    period_end: str,
    target_accn: str,
) -> Dict[str, Optional[XbrlFact]]:
    return {
        m: resolve_metric(companyfacts, m, period_end=period_end, target_accn=target_accn)
        for m in METRIC_TAG_MAP
    }

# ---------------------------------------------------------------------------
# 5. Derived metric calculations
# ---------------------------------------------------------------------------

def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def compute_derived_metrics(raw: Dict[str, Optional[XbrlFact]]) -> Dict[str, Optional[float]]:
    """
    Compute growth, margin, and efficiency ratios from the raw XBRL facts.
    Returns a flat dictionary of metric_name -> value (or None).
    """
    def v(metric: str) -> Optional[float]:
        f = raw.get(metric)
        return f.value if f else None

    rev = v("Revenue_total")
    gp = v("GrossProfit")
    oi = v("OperatingIncomeLoss")
    ni = v("NetIncomeLoss")
    eps_d = v("EarningsPerShareDiluted")
    eps_b = v("EarningsPerShareBasic")
    rnd = v("ResearchAndDevelopmentExpense")
    sm = v("SellingAndMarketingExpense")
    ga = v("GeneralAndAdministrativeExpense")
    opex = v("OperatingExpenses")
    capex = v("PaymentsToAcquirePropertyPlantAndEquipment")
    cfo = v("NetCashProvidedByUsedInOperatingActivities")
    da = v("DepreciationDepletionAndAmortization")
    assets = v("Assets")
    equity = v("StockholdersEquity")
    shares_d = v("WeightedAverageNumberOfDilutedSharesOutstanding")

    # EBITDA = Operating income + D&A (if D&A is available)
    ebitda = None
    if oi is not None and da is not None:
        ebitda = oi + da

    # Free cash flow = CFO − |Capex|
    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo - abs(capex)

    out: Dict[str, Optional[float]] = {
        # Growth & scale (levels already in raw; growth rates require prior year,
        # so they are left as None here and can be computed in a later stage
        # once two years of facts are available.)
        "Revenue_total": rev,
        "GrossProfit": gp,
        "OperatingIncome": oi,
        "NetIncome": ni,
        "DilutedEPS": eps_d,
        "BasicEPS": eps_b,
        # Profitability
        "GrossMargin": _safe_div(gp, rev),
        "OperatingMargin": _safe_div(oi, rev),
        "NetMargin": _safe_div(ni, rev),
        "ReturnOnAssets": _safe_div(ni, assets),
        "ReturnOnEquity": _safe_div(ni, equity),
        "EBITDA": ebitda,
        "EBITDA_margin": _safe_div(ebitda, rev),
        # Operating efficiency
        "R&D_expense_to_revenue": _safe_div(rnd, rev),
        "SalesAndMarketing_to_revenue": _safe_div(sm, rev),
        "G&A_to_revenue": _safe_div(ga, rev),
        "TotalOperatingExpense_to_revenue": _safe_div(opex, rev),
        "AssetTurnover": _safe_div(rev, assets),
        "RevenuePerEmployee": None,  # requires employee count; inject separately
        "Capex_to_revenue": _safe_div(capex, rev),
        "OperatingCashFlow_to_revenue": _safe_div(cfo, rev),
        "FreeCashFlowMargin": _safe_div(fcf, rev),
    }
    return out

# ---------------------------------------------------------------------------
# 6. HTML / iXBRL extraction (replaces PDF table extraction)
# ---------------------------------------------------------------------------

_XBRL_NAMESPACES = {
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
    "us-gaap": "http://fasb.org/us-gaap/2024",
    "ifrs-full": "http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full",
}

# ---------------------------------------------------------------------------
# DOM helpers for fact-level provenance recovery
# ---------------------------------------------------------------------------

def _compute_page_map(soup) -> Dict[int, int]:
    """
    Map id(tag) -> 1-based page number for every <ix:nonFraction> in the
    document.  Counts <hr> and elements with page-break-* styles in a single
    pass, so the cost is O(total_tags).  Returns {} if the filing has no
    page-break markers (older filings, some 20-Fs).
    """
    all_tags = soup.find_all(True)
    page_breaks_at: List[int] = []
    for i, el in enumerate(all_tags):
        style = (el.get("style") or "").lower().replace(" ", "")
        if el.name == "hr" or "page-break-before" in style or "page-break-after" in style:
            page_breaks_at.append(i)
    if not page_breaks_at:
        return {}

    fact_pages: Dict[int, int] = {}
    page = 1
    bi = 0
    for i, el in enumerate(all_tags):
        while bi < len(page_breaks_at) and page_breaks_at[bi] <= i:
            page += 1
            bi += 1
        if el.name and "nonfraction" in el.name.lower():
            fact_pages[id(el)] = page
    return fact_pages


def _column_index(cell, row) -> Optional[int]:
    """0-based column index of `cell` in `row`, honoring colspan."""
    if cell is None or row is None:
        return None
    idx = 0
    for c in row.find_all(["td", "th"], recursive=False):
        if c is cell:
            return idx
        try:
            idx += int(c.get("colspan", 1) or 1)
        except (TypeError, ValueError):
            idx += 1
    return None


def _header_for_column(table, col_idx: int) -> str:
    if table is None or col_idx is None:
        return ""

    NUMERIC_RE = re.compile(r"[\s\d.,\-–—()$%\u00a0]+")

    def cell_text_at(row, target):
        pos = 0
        for c in row.find_all(["td", "th"], recursive=False):
            try:
                span = int(c.get("colspan", 1) or 1)
            except (TypeError, ValueError):
                span = 1
            if pos <= target < pos + span:
                return c.get_text(" ", strip=True)
            pos += span
        return ""

    # Pass 1: rows with at least one <th>
    for row in table.find_all("tr"):
        if not row.find_all("th", recursive=False):
            continue
        txt = cell_text_at(row, col_idx)
        if txt and not NUMERIC_RE.fullmatch(txt):
            return txt[:80]

    # Pass 2: header-like <td> rows — the first few <tr>s where every
    # cell after the first is non-numeric (so "2025" "2024" "2023" wins,
    # but "391,035" "394,328" rows are rejected).
    for row in table.find_all("tr")[:8]:
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        if any(NUMERIC_RE.fullmatch(t) for t in texts[1:] if t):
            continue
        txt = cell_text_at(row, col_idx)
        if txt and not NUMERIC_RE.fullmatch(txt):
            return txt[:80]

    return ""


def _find_row_and_col_labels(nf_tag) -> Tuple[str, str]:
    """
    Walk the DOM around an <ix:nonFraction> to recover:
      - row_label: text of the leftmost non-numeric cell in the same <tr>
      - col_label: text of the matching <th> in the enclosing <table>'s header
    """
    tr = nf_tag.find_parent("tr")
    if tr is None:
        return "", ""

    our_cell = nf_tag.find_parent(["td", "th"])

    row_label = ""
    for cell in tr.find_all(["td", "th"], recursive=False):
        if cell is our_cell:
            continue
        txt = cell.get_text(" ", strip=True)
        if not txt:
            continue
        # Skip pure numeric / dash cells — they're values, not labels
        if re.fullmatch(r"[\s\d.,\-–—()$%\u00a0]+", txt):
            continue
        row_label = txt[:160]
        break

    col_label = ""
    table = nf_tag.find_parent("table")
    if table is not None and our_cell is not None:
        col_idx = _column_index(our_cell, tr)
        col_label = _header_for_column(table, col_idx)

    return row_label, col_label


def _table_index_map(soup) -> Dict[int, str]:
    """Stable table_id per <table> element: 'tbl_0', 'tbl_1', …"""
    return {id(t): f"tbl_{i}" for i, t in enumerate(soup.find_all("table"))}

def extract_ixbrl_facts(html_bytes: bytes, ticker: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html_bytes, "lxml-xml")
    if not soup.find_all(re.compile(r"(?:^|:)nonfraction$", re.I)):
        soup = BeautifulSoup(html_bytes, "lxml")

    # --- 1. Contexts -------------------------------------------------------
    contexts: Dict[str, Dict[str, Any]] = {}
    for ctx_el in soup.find_all(re.compile(r"(?:^|:)context$", re.I)):
        ctx_id = ctx_el.get("id")
        if not ctx_id:
            continue
        dims: Dict[str, str] = {}
        for member_el in ctx_el.find_all(re.compile(r"(?:^|:)explicitMember$", re.I)):
            axis = member_el.get("dimension")
            if axis:
                dims[axis] = member_el.get_text(strip=True)
        period: Dict[str, str] = {}
        for field, key in (("startDate", "start"), ("endDate", "end"), ("instant", "instant")):
            period_el = ctx_el.find(re.compile(rf"(?:^|:){field}$", re.I))
            if period_el is not None:
                period[key] = period_el.get_text(strip=True)
        contexts[ctx_id] = {"dimensions": dims, "period": period}

    # --- 2. Precompute per-table section + per-fact page in one pass ------
    table_sections: Dict[int, str] = {
        id(t): (_nearest_financial_section(t) or "")
        for t in soup.find_all("table")
    }
    table_ids = _table_index_map(soup)
    page_map = _compute_page_map(soup)

    # --- 3. Numeric facts --------------------------------------------------
    nf_tags = soup.find_all(re.compile(r"(?:^|:)nonfraction$", re.I))
    print(f"[{ticker}] iXBRL parse: {len(nf_tags)} <nonFraction> tags, "
          f"{len(contexts)} contexts, {len(page_map)} facts paged")

    facts: List[Dict[str, Any]] = []
    for nf_tag in nf_tags:
        if (nf_tag.get("xsi:nil") or nf_tag.get(
                "{http://www.w3.org/2001/XMLSchema-instance}nil")) in ("true", "1"):
            continue
        concept = nf_tag.get("name") or ""
        if not concept:
            continue
        ctx_ref = nf_tag.get("contextRef") or nf_tag.get("contextref") or ""
        unit_ref = nf_tag.get("unitRef") or nf_tag.get("unitref") or ""

        raw = re.sub(r"[^\d.\-]", "", nf_tag.get_text())
        if not raw or raw in ("-", ".", "-."):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        scale = int(nf_tag.get("scale", 0) or 0)
        value *= 10 ** scale
        if nf_tag.get("sign") == "-":
            value = -value

        ctx = contexts.get(ctx_ref) or {}
        table = nf_tag.find_parent("table")
        row_label, col_label = _find_row_and_col_labels(nf_tag)

        facts.append({
            "ticker": ticker,
            "concept": concept,
            "value": value,
            "unit": unit_ref,
            "dimensions": ctx.get("dimensions") or {},
            "period": ctx.get("period") or {},
            "context_ref": ctx_ref,
            # --- provenance fields ---
            "fact_id": nf_tag.get("id") or None,
            "table_id": table_ids.get(id(table)) if table is not None else None,
            "row_label": row_label or None,
            "col_label": col_label or None,
            "section_path": table_sections.get(id(table)) if table is not None else None,
            "source_page": page_map.get(id(nf_tag)),
        })

    return facts


FINANCIAL_STATEMENT_MARKERS = [
    "consolidated statements of operations",
    "consolidated statements of income",
    "consolidated statements of earnings",
    "consolidated income statements",
    "consolidated balance sheets",
    "consolidated statements of financial position",
    "consolidated statements of cash flows",
    "consolidated statements of comprehensive income",
    "statements of operations",
    "statements of income",
    "income statements",
    "balance sheets",
    "statements of cash flows",
]

TOC_ROW_RE = re.compile(
    r"^(item\s+\d|part\s+[ivx]+|exhibit\b|signatures?\b|table\s+of\s+contents\b|page\b)",
    re.IGNORECASE,
)

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "th", "div", "p", "span"}


def _is_heading_like(el) -> bool:
    """Accept a tag if it looks like a heading: heading tag, bold style,
    or a class containing bold/header/title."""
    if el.name in ("h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "th"):
        return True
    style = (el.get("style") or "").lower().replace(" ", "")
    if "font-weight:bold" in style or "font-weight:700" in style:
        return True
    cls = " ".join(el.get("class") or []).lower()
    if any(k in cls for k in ("bold", "header", "title", "heading")):
        return True
    return False


def _is_financial_statement_heading(el) -> bool:
    if not _is_heading_like(el):
        return False
    txt = el.get_text(" ", strip=True)
    if not txt or len(txt) > 200:
        return False
    low = txt.lower()
    if not any(m in low for m in FINANCIAL_STATEMENT_MARKERS):
        return False
    # Extra signal: all-uppercase, or contains a four-digit year, or has bold style
    if txt.isupper() or re.search(r"\b(19|20)\d{2}\b", txt):
        return True
    style = (el.get("style") or "").lower().replace(" ", "")
    return "font-weight:bold" in style or "font-weight:700" in style


def _nearest_financial_section(table) -> Optional[str]:
    for i, el in enumerate(table.find_all_previous(True)):
        if i > 200:
            return None
        if _is_financial_statement_heading(el):
            return next(m for m in FINANCIAL_STATEMENT_MARKERS
                        if m in el.get_text(" ", strip=True).lower())
    return None


def extract_html_tables(
    html_bytes: bytes,
    ticker: str,
    fiscal_year: int,
) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html_bytes, "lxml-xml")
    if not soup.find_all("table"):
        soup = BeautifulSoup(html_bytes, "lxml")
    tables = soup.find_all("table")

    try:
        parsed_frames = pd.read_html(html_bytes, flavor="lxml")
    except Exception:
        parsed_frames = []
    if len(parsed_frames) < len(tables):
        parsed_frames += [None] * (len(tables) - len(parsed_frames))

    rows: List[Dict[str, Any]] = []
    for t_idx, table in enumerate(tables):
        section = _nearest_financial_section(table)
        if section is None:
            continue

        df = parsed_frames[t_idx]
        if df is None or df.empty or df.shape[1] < 2:
            continue

        year_col_idx: Optional[int] = None
        for c_idx, col in enumerate(df.columns):
            if str(fiscal_year) in str(col):
                year_col_idx = c_idx
                break

        for r_idx in range(len(df)):
            row = df.iloc[r_idx].tolist()
            if not row:
                continue
            label = str(row[0]).strip() if row[0] is not None else ""
            if not label or len(label) > 160:
                continue
            if TOC_ROW_RE.match(label):
                continue
            if label.lower() in ("nan", "none", "—", "-"):
                continue

            value = None                       # ← init BEFORE the loop
            col_label = ""
            if year_col_idx is not None and year_col_idx < len(row):
                candidates = [(year_col_idx, row[year_col_idx])]
            else:
                candidates = [(i, v) for i, v in enumerate(row[1:], start=1)]

            for c_idx, cell in candidates:
                if cell is None:
                    continue
                txt = re.sub(r"[^\d.\-]", "", str(cell))
                if not txt or txt in ("-", ".", "-."):
                    continue
                try:
                    value = float(txt)
                    col_label = str(df.columns[c_idx]) if c_idx < len(df.columns) else ""
                    break
                except ValueError:
                    continue
            if value is None:
                continue
            if abs(value) < 1000:              # page numbers / section numbers
                continue

            rows.append({
                "ticker": ticker,
                "table_index": t_idx,
                "row_index": r_idx,
                "row_label": label,
                "col_label": col_label,
                "value": value,
                "section_path": section,
            })
    return rows


def concept_to_metric(concept: str) -> Optional[str]:
    """Map an XBRL concept (e.g. 'us-gaap:ResearchAndDevelopmentExpense')
    back to our internal metric name."""
    bare = concept.split(":")[-1] if ":" in concept else concept
    for metric, tags in METRIC_TAG_MAP.items():
        if bare in tags:
            return metric
    return None



# ---------------------------------------------------------------------------
# 7. Evidence unit construction
# ---------------------------------------------------------------------------

@dataclass
class EvidenceUnit:
    unit_id: str
    unit_type: str
    ticker: str
    cik: str
    fiscal_year: int
    metric: str
    concept: str
    value: Optional[float]
    unit: str
    form: str
    accession_number: str
    filing_date: str
    source_page: Optional[int] = None
    fact_id: Optional[str] = None
    table_id: Optional[str] = None
    row_label: Optional[str] = None
    col_label: Optional[str] = None
    dimensions: Dict[str, str] = field(default_factory=dict)
    section_path: Optional[str] = None
    mapping_method: str = "xbrl_tag"
    validation_status: str = "pending"
    linked_footnote_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_unit_id(ticker: str, fiscal_year: int, source: str, row_idx: int = 0) -> str:
    """Deterministic unit_id."""
    raw = f"{ticker}_{fiscal_year}_{source}_{row_idx}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def build_evidence_units_for_company(
    ticker: str,
    fiscal_year: int = TARGET_FISCAL_YEAR,
) -> List[EvidenceUnit]:
    cik = CIK_MAP[ticker]

    # --- 0. Pin the filing to the target fiscal year ----------------------
    filing = find_filing_for_year(cik, fiscal_year=fiscal_year)
    if not filing:
        raise RuntimeError(
            f"{ticker}: no 10-K with reportDate year == {fiscal_year} found"
        )
    period_end = filing["report_date"]
    print(f"[{ticker}] FY{fiscal_year} 10-K: {filing['accession_number']} "
          f"(period end {period_end}, filed {filing['filing_date']})")

    units: List[EvidenceUnit] = []

    # --- 1. companyfacts path (consolidated metrics) ---------------------
    facts_json = fetch_companyfacts(cik)
    raw = resolve_all_metrics(
        facts_json,
        period_end=period_end,
        target_accn=filing["accession_number"],
    )
    for metric, fact in raw.items():
        if fact is None:
            continue
        units.append(EvidenceUnit(
            unit_id=make_unit_id(ticker, fiscal_year, f"xbrl_{metric}"),
            unit_type="xbrl_fact",
            ticker=ticker, cik=cik, fiscal_year=fiscal_year,
            metric=metric, concept=fact.tag, value=fact.value, unit=fact.unit,
            form=fact.form,
            accession_number=fact.accession_number or filing["accession_number"],
            filing_date=fact.filed or filing["filing_date"],
            dimensions=fact.dimensions,
            mapping_method="xbrl_tag",
            validation_status="passed",
        ))

    # --- 2. derived metrics (same period) --------------------------------
    for metric, val in compute_derived_metrics(raw).items():
        if val is None or any(u.metric == metric for u in units):
            continue
        units.append(EvidenceUnit(
            unit_id=make_unit_id(ticker, fiscal_year, f"derived_{metric}"),
            unit_type="xbrl_fact", ticker=ticker, cik=cik,
            fiscal_year=fiscal_year, metric=metric, concept=f"derived:{metric}",
            value=val, unit="ratio",
            form=filing["form"],
            accession_number=filing["accession_number"],
            filing_date=filing["filing_date"],
            mapping_method="calculation",
            validation_status="passed",
        ))

    # --- 3. HTML / iXBRL path (dimensional + row-level) ------------------
    ixbrl_facts: List[Dict[str, Any]] = []   # hoisted so Stage 4 backfill can use it
    if not filing["primary_document"].lower().endswith(".pdf"):
        try:
            print(f"[{ticker}] Downloading {filing['primary_document']} …")
            html_bytes = download_filing_document(
                cik, filing["accession_number"], filing["primary_document"]
            )

            # 3a. iXBRL numeric facts (with provenance)
            ixbrl_facts = extract_ixbrl_facts(html_bytes, ticker)
            kept = rejected = 0
            for i, fact in enumerate(ixbrl_facts):
                metric = concept_to_metric(fact["concept"])
                if metric is None:
                    continue

                # Rejection runs BEFORE period filter so the adversarial case
                # (segment revenue masquerading as Revenue_total) is always
                # visible in the parquet.
                if metric in CONSOLIDATED_METRICS and fact["dimensions"]:
                    units.append(EvidenceUnit(
                        unit_id=make_unit_id(ticker, fiscal_year, f"ixbrl_rej_{i}"),
                        unit_type="xbrl_fact", ticker=ticker, cik=cik,
                        fiscal_year=fiscal_year, metric=metric, concept=fact["concept"],
                        value=fact["value"], unit=fact["unit"],
                        form=filing["form"],
                        accession_number=filing["accession_number"],
                        filing_date=filing["filing_date"],
                        dimensions=fact["dimensions"],
                        fact_id=fact.get("fact_id"),
                        table_id=fact.get("table_id"),
                        row_label=fact.get("row_label"),
                        col_label=fact.get("col_label"),
                        section_path=fact.get("section_path"),
                        source_page=fact.get("source_page"),
                        mapping_method="ixbrl_tag",
                        validation_status="rejected",
                    ))
                    rejected += 1
                    continue

                # Only ACCEPTED facts get period-filtered
                if (fact.get("period") or {}).get("end") != period_end:
                    continue

                # Here `fact` is a dict; the ternary is safe. A consolidated
                # fact that isn't anchored in a primary-statement table
                # (e.g. a segment-note "Total net sales") becomes "pending"
                # rather than "passed".
                units.append(EvidenceUnit(
                    unit_id=make_unit_id(ticker, fiscal_year, f"ixbrl_{i}"),
                    unit_type="xbrl_fact", ticker=ticker, cik=cik,
                    fiscal_year=fiscal_year, metric=metric, concept=fact["concept"],
                    value=fact["value"], unit=fact["unit"],
                    form=filing["form"],
                    accession_number=filing["accession_number"],
                    filing_date=filing["filing_date"],
                    dimensions=fact["dimensions"],
                    fact_id=fact.get("fact_id"),
                    table_id=fact.get("table_id"),
                    row_label=fact.get("row_label"),
                    col_label=fact.get("col_label"),
                    section_path=fact.get("section_path"),
                    source_page=fact.get("source_page"),
                    mapping_method="ixbrl_tag",
                    validation_status=(
                        "pending" if fact["dimensions"]
                        else "pending" if not fact.get("section_path")
                        else "passed"
                    ),
                ))
                kept += 1
            print(f"[{ticker}] iXBRL: {kept} kept, {rejected} rejected (dimension_mismatch)")

            # 3b. HTML table rows (row-level provenance)
            html_rows = extract_html_tables(html_bytes, ticker, fiscal_year=fiscal_year)
            for i, row in enumerate(html_rows):
                metric_guess = ""
                low = row["row_label"].lower()
                for m, aliases in (
                    ("ResearchAndDevelopmentExpense", ["research and development", "r&d"]),
                    ("Revenue_product", ["products"]),
                    ("Revenue_service", ["services"]),
                    ("Revenue_total", ["total net sales", "total revenue", "net sales", "revenue"]),
                    ("GrossProfit", ["gross profit", "gross margin"]),
                    ("OperatingIncomeLoss", ["operating income", "income from operations"]),
                    ("NetIncomeLoss", ["net income"]),
                    ("CostOfRevenue", ["cost of sales", "cost of revenue", "cost of goods"]),
                    ("OperatingExpenses", ["total operating expenses"]),
                ):
                    if any(a in low for a in aliases):
                        metric_guess = m
                        break
                if not metric_guess:
                    continue
                units.append(EvidenceUnit(
                    unit_id=make_unit_id(ticker, fiscal_year, "html_table", i),
                    unit_type="table_row", ticker=ticker, cik=cik,
                    fiscal_year=fiscal_year, metric=metric_guess,
                    concept="html_table_row", value=row["value"], unit="USD",
                    form=filing["form"],
                    accession_number=filing["accession_number"],
                    filing_date=filing["filing_date"],
                    table_id=f"tbl_{row['table_index']}",
                    row_label=row["row_label"],
                    col_label=row["col_label"],
                    section_path=row["section_path"],
                    source_page=row.get("source_page"),
                    mapping_method="html_row_label",
                    validation_status="pending",
                ))
        except Exception as exc:
            print(f"[{ticker}] HTML extraction skipped: {exc}")

    # --- 4. Backfill provenance onto xbrl_tag units from iXBRL siblings --
    # companyfacts carries no DOM metadata, but every consolidated fact
    # also appears in the HTML as an <ix:nonFraction>. Match on
    # (bare concept, rounded value) and copy row_label / col_label /
    # section_path / source_page / fact_id / table_id across.
    if ixbrl_facts:
        ixbrl_lookup: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for f in ixbrl_facts:
            bare = f["concept"].split(":")[-1]
            key = (bare, round(f["value"]))
            ixbrl_lookup.setdefault(key, f)   # keep first (consolidated wins)

        backfilled = 0
        for u in units:
            if u.mapping_method != "xbrl_tag" or u.value is None:
                continue
            bare = (u.concept or "").split(":")[-1]
            match = ixbrl_lookup.get((bare, round(u.value)))
            if not match:
                continue
            if not u.fact_id:           u.fact_id = match.get("fact_id")
            if not u.row_label:         u.row_label = match.get("row_label")
            if not u.col_label:         u.col_label = match.get("col_label")
            if not u.section_path:      u.section_path = match.get("section_path")
            if u.source_page is None:   u.source_page = match.get("source_page")
            if not u.table_id:          u.table_id = match.get("table_id")
            backfilled += 1
        print(f"[{ticker}] Backfilled provenance on {backfilled} xbrl_tag units")

    print(f"[{ticker}] Built {len(units)} evidence units for FY{fiscal_year}.")
    return units


# ---------------------------------------------------------------------------
# 8. Orchestration entry point
# ---------------------------------------------------------------------------

def run_ingestion(
    tickers: List[str] = TICKERS,
    fiscal_year: int = TARGET_FISCAL_YEAR,
    output_dir: str = "data/evidence_units",
) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    all_units: List[EvidenceUnit] = []
    for ticker in tickers:
        try:
            all_units.extend(build_evidence_units_for_company(ticker, fiscal_year=fiscal_year))
        except Exception as exc:
            print(f"[{ticker}] FAILED: {exc}")

    with (out / "evidence_units.jsonl").open("w", encoding="utf-8") as fh:
        for u in all_units:
            fh.write(json.dumps(u.to_dict(), default=str) + "\n")

    df = pd.DataFrame([u.to_dict() for u in all_units])
    if "dimensions" in df.columns:
        df["dimensions"] = df["dimensions"].apply(json.dumps)
    df.to_parquet(out / "evidence_units.parquet", index=False)
    print(f"\nWrote {len(all_units)} units → {out}/evidence_units.{{jsonl,parquet}}")


if __name__ == "__main__":
    run_ingestion()