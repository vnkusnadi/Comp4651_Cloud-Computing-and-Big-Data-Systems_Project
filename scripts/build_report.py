"""Build Person B's Week-4 deliverable report (DOCX).

Usage (from the repository root):
    python scripts/build_report.py
Output: PersonB_Week4_Deliverable.docx at the repository root.
"""
from pathlib import Path

from docx import Document

OUT = Path(__file__).resolve().parents[1] / "PersonB_Week4_Deliverable.docx"

doc = Document()

doc.add_heading("SectorView \u2014 Person B Week 4 Deliverable", level=0)
p = doc.add_paragraph()
r = p.add_run("IT Sector Schema v0.1 \u00b7 Concept Mapping \u00b7 Alignment Validation")
r.bold = True
doc.add_paragraph("Course: COMP4651 Cloud Computing and Big Data Systems \u00b7 Group 16\n"
                  "Scope: Information Technology (GICS 45), FY2025 10-Ks \u2014 AAPL, MSFT, NVDA, AVGO, CSCO\n"
                  "Inputs: Person A evidence_units (862 units) + original 10-K filings (staging)")

# ---------------------------------------------------------------------------
doc.add_heading("1. Person B Scope and Status", level=1)
doc.add_paragraph("Per the course specification, Person B owns the sector schema, mapping, "
                  "and alignment validation. Status of the four responsibilities:")
t = doc.add_table(rows=1, cols=3)
t.style = "Table Grid"
hdr = t.rows[0].cells
for i, h in enumerate(("Responsibility", "Deliverable", "Status")):
    hdr[i].text = h
    hdr[i].paragraphs[0].runs[0].bold = True
rows = [
    ("Sector schema (information_technology.yaml)",
     "IT schema v0.1: 24 base concepts, 17 derived metrics, 3 category bundles, "
     "plus an AAPL/MSFT verified company dictionary",
     "Done"),
    ("Deterministic mapping + embedding fallback",
     "src/mapping/deterministic.py (xbrl_tag \u2192 exact_label \u2192 normalized_label) "
     "and src/mapping/embeddings.py (candidate-only, verification-gated)",
     "Done"),
    ("Calculation-constraint validator",
     "src/align/validate.py: validate_alignment (\u00a74.3), revenue reconciliation, "
     "accounting identities, R&D rules, alignment edges",
     "Done"),
    ("test_total_vs_segment.py",
     "Segment revenue rejected for Revenue_total; total revenue selected",
     "Done"),
]
for row in rows:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v

# ---------------------------------------------------------------------------
doc.add_heading("2. IT Sector Schema v0.1", level=1)
doc.add_paragraph("configs/sectors/information_technology.yaml \u2014 the sector dictionary is "
                  "the single source of truth consumed by mapping, validation, retrieval, and evaluation.")
t = doc.add_table(rows=1, cols=3)
t.style = "Table Grid"
hdr = t.rows[0].cells
for i, h in enumerate(("Tier", "Contents", "Rules")):
    hdr[i].text = h
    hdr[i].paragraphs[0].runs[0].bold = True
rows = [
    ("Base concepts (24)",
     "Raw numbers read from the 10-K: revenue, cost of revenue, gross profit, operating "
     "income, net income, EPS, assets, equity, R&D, S&M, G&A, OpEx, capex, operating cash "
     "flow, D&A, and more. Each carries canonical name, aliases, XBRL tags, unit type, "
     "period type, dimension requirement, and validation rules.",
     "Tag \u2192 exact label \u2192 normalized label matching; consolidated-only concepts "
     "reject any dimensioned unit (dimension_mismatch)."),
    ("Derived metrics (17)",
     "Formula-backed ratios: GrossMargin, OperatingMargin, NetMargin, ROA, ROE, EBITDA, "
     "R&D_expense_to_revenue, Capex_to_revenue, FreeCashFlowMargin, AssetTurnover, "
     "Product/Service mix, etc. Each has formula_id, numerator and denominator concepts.",
     "Computed by code from verified base concepts \u2014 arithmetic is never inferred, "
     "the LLM never calculates."),
    ("Category bundles (3)",
     "Scale (13 members, higher_is_larger), Profitability (12 members, higher_is_better), "
     "Operating efficiency (8 members, profile comparison \u2014 high R&D intensity is "
     "descriptive, not \u201cgood\u201d).",
     "Categories are the only user-facing entry point; no metric-level query path."),
]
for row in rows:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v

doc.add_paragraph("Spec-required concepts: R&D_expense \u2713, Revenue_total \u2713, "
                  "Revenue_segment \u2713 (instantiated as Revenue_product / Revenue_service), "
                  "GrossProfit \u2713, Capex \u2713, SupplyChainConcentrationRisk \u2014 declared "
                  "in the schema as a narrative concept; joinable once Person A emits "
                  "paragraph/footnote units.")

# ---------------------------------------------------------------------------
doc.add_heading("3. Apple / Microsoft R&D and Revenue Mappings", level=1)
doc.add_paragraph("Every row below was verified against the original 10-K filings "
                  "(scripts/verify_dictionary.py, 40/40 checks passed).")
t = doc.add_table(rows=1, cols=5)
t.style = "Table Grid"
hdr = t.rows[0].cells
for i, h in enumerate(("Concept", "AAPL (aapl-20250927.htm)", "AAPL value", "MSFT (msft-20250630.htm)", "MSFT value")):
    hdr[i].text = h
    hdr[i].paragraphs[0].runs[0].bold = True
rows = [
    ("Revenue_total",
     "\u201cTotal net sales\u201d, p.31, tag RevenueFromContractWithCustomerExcludingAssessedTax",
     "416,161M",
     "\u201cTotal revenue\u201d, p.50, same tag",
     "281,724M"),
    ("R&D_expense",
     "\u201cResearch and development\u201d, p.31, tag ResearchAndDevelopmentExpense",
     "34,550M",
     "\u201cResearch and development\u201d, p.50, same tag",
     "32,488M"),
    ("GrossProfit",
     "\u201cGross margin\u201d, p.31, tag GrossProfit",
     "195,201M",
     "\u201cGross margin\u201d, p.50, same tag",
     "193,893M"),
    ("Capex",
     "\u201cPayments for acquisition of property, plant and equipment\u201d, p.35",
     "12,715M",
     "\u201cAdditions to property and equipment\u201d, p.53",
     "64,551M"),
    ("R&D intensity (R&D / Revenue)",
     "computed from mapped values",
     "8.30%",
     "computed from mapped values",
     "11.53%"),
]
for row in rows:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v
doc.add_paragraph("R&D-intensity question answered by data: MSFT (11.53%) invests a larger "
                  "share of revenue in R&D than AAPL (8.30%). Recomputed intensities equal "
                  "Person A\u2019s derived units to 1e-9.")

# ---------------------------------------------------------------------------
doc.add_heading("4. Rejected Segment-Revenue Mappings (with Reasons)", level=1)
doc.add_paragraph("The adversarial test case: segment rows masquerading as Revenue_total "
                  "are rejected with reason dimension_mismatch and kept \u2014 never dropped silently.")
t = doc.add_table(rows=1, cols=5)
t.style = "Table Grid"
hdr = t.rows[0].cells
for i, h in enumerate(("Company", "Row (dimension member)", "Value", "Disposition", "Reason")):
    hdr[i].text = h
    hdr[i].paragraphs[0].runs[0].bold = True
rows = [
    ("AAPL", "Products (us-gaap:ProductMember)", "307,003M",
     "rejected as Revenue_total; mapped Revenue_product", "dimension_mismatch"),
    ("AAPL", "Services (us-gaap:ServiceMember)", "109,158M",
     "rejected as Revenue_total; mapped Revenue_service", "dimension_mismatch"),
    ("MSFT", "Product (us-gaap:ProductMember)", "63,946M",
     "rejected as Revenue_total; mapped Revenue_product", "dimension_mismatch"),
    ("MSFT", "Service and other (us-gaap:ServiceOtherMember)", "217,778M",
     "rejected as Revenue_total; mapped Revenue_service", "dimension_mismatch"),
    ("MSFT", "Intelligent Cloud segment (us-gaap:StatementBusinessSegmentsAxis)", "106,265M",
     "rejected as Revenue_total", "dimension_mismatch"),
]
for row in rows:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v
doc.add_paragraph("Why it matters: reading MSFT\u2019s Intelligent Cloud segment (106,265M) as its "
                  "total revenue would show a 95.8% net margin instead of the correct 36.1%. "
                  "Segment reconciliation (product + service \u2248 total) holds at 0.00% gap for all "
                  "five companies. Pipeline-wide, 494 rejected candidate mappings were recorded, "
                  "all with reason dimension_mismatch.")

# ---------------------------------------------------------------------------
doc.add_heading("5. Stage 4 Mapping Results (862 evidence units)", level=1)
t = doc.add_table(rows=1, cols=2)
t.style = "Table Grid"
hdr = t.rows[0].cells
for i, h in enumerate(("Metric", "Value")):
    hdr[i].text = h
    hdr[i].paragraphs[0].runs[0].bold = True
rows = [
    ("Mappings produced", "490 (453 passed, 37 pending)"),
    ("Rejected candidate mappings", "494 (all dimension_mismatch)"),
    ("Unmapped", "22 (20 derived-level duplicates kept out by design + 2 semantic gaps)"),
    ("Mapping methods", "xbrl_tag 387 \u00b7 calculation 66 \u00b7 exact_label 37"),
    ("Embedding fallback proposals", "3 for 1 unit (NVDA \u201cRevenue\u201d) \u2014 Revenue_total "
     "proposal verified by value confirmation; AVGO \u201cIPR&D\u201d correctly gets no candidate"),
]
for row in rows:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v

# ---------------------------------------------------------------------------
doc.add_heading("6. Alignment Validation (Stage 5)", level=1)
doc.add_paragraph("validate_alignment(candidate_a, candidate_b, tolerance=0.02): "
                  "dimension conflict \u2192 rejected (dimension_mismatch); failed numeric continuity "
                  "\u2192 rejected (calculation_gap_exceeded); otherwise accepted (passed).")
t = doc.add_table(rows=1, cols=4)
t.style = "Table Grid"
hdr = t.rows[0].cells
for i, h in enumerate(("Company", "Gross profit identity", "Operating income identity", "EPS identity")):
    hdr[i].text = h
    hdr[i].paragraphs[0].runs[0].bold = True
rows = [
    ("AAPL", "0.00%", "0.00%", "0.07%"),
    ("MSFT", "0.00%", "0.00%", "0.01%"),
    ("NVDA", "0.00%", "0.00%", "0.06%"),
    ("AVGO", "0.00%", "0.00%", "0.10%"),
    ("CSCO", "0.00%", "0.00%", "0.15%"),
]
for row in rows:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v
doc.add_paragraph("R&D rules: consolidated-only, never footnote-only, R&D \u2264 OperatingExpenses "
                  "(component check), anchored in a primary income statement. Cross-company "
                  "alignment edges: 10 Revenue_total edges + 10 R&D edges, all passed; each edge "
                  "carries unit_a, unit_b, concept, period, validation_status, and gap.")

# ---------------------------------------------------------------------------
doc.add_heading("7. Test Evidence", level=1)
t = doc.add_table(rows=1, cols=3)
t.style = "Table Grid"
hdr = t.rows[0].cells
for i, h in enumerate(("Test file", "Tests", "Result")):
    hdr[i].text = h
    hdr[i].paragraphs[0].runs[0].bold = True
rows = [
    ("tests/test_total_vs_segment.py", "5", "5/5 passed"),
    ("tests/test_deterministic_mapping.py", "6", "6/6 passed"),
    ("tests/test_embeddings.py", "11", "11/11 passed"),
    ("tests/test_rnd_intensity.py", "7", "7/7 passed"),
    ("Total", "29", "29/29 passed"),
]
for row in rows:
    cells = t.add_row().cells
    for i, v in enumerate(row):
        cells[i].text = v
doc.add_paragraph("All tests run against Person A\u2019s real evidence_units (no mocks) and can run "
                  "standalone (python tests/<file>.py) or under pytest.")

# ---------------------------------------------------------------------------
doc.add_heading("8. Known Gaps and Handoffs", level=1)
for item in (
    "MSFT DepreciationAndAmortization: MSFT tags the line with the company extension "
    "msft:DepreciationAmortizationAndOther (34,153M) instead of the standard tag. Person A should add "
    "the extension tag to METRIC_TAG_MAP and re-ingest; the schema already lists it as a fallback.",
    "SupplyChainConcentrationRisk and paragraph_table_agreement need paragraph/footnote units, "
    "which Person A\u2019s v1 dataset does not emit yet (Stage 3 v2).",
    "Alignment-edge schema (validate.py build_alignment_edges) is the read interface for Person C.",
    "Team environments should install pytest, PyYAML, and sentence-transformers; the pipeline "
    "falls back to a bundled YAML parser and a character-n-gram embedder so it runs anywhere.",
    "Staging filings (~18 MB) are git-ignored; re-download with scripts/download_filings.py.",
):
    doc.add_paragraph(item, style="List Bullet")

doc.save(OUT)
print("saved:", OUT)
