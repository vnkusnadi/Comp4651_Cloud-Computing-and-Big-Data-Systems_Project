# Top-AI baseline: the 3 prompts to paste

Paste each block into its own new chat inside the claude.ai project that holds the five 10-K PDFs.
Start a stopwatch when you press Enter and stop it when the answer is complete. Write the seconds down.

## Chat 1: Scale (11 questions)

```
Answer each question below using only the five 10-K reports in this project
(Apple, Microsoft, Nvidia, Broadcom, Cisco; the fiscal 2025 annual reports).
Answer every question on its own; do not reuse numbers you did not look up for that question.

Reply with JSON Lines only: one JSON object per line, one line per question, nothing else.
Each line has this shape:
{"question_id": "Q01", "verdict_type": "dominant" | "tradeoff" | "profile", "winner": "<ticker or null>",
 "answer_text": "<2-3 sentences>",
 "numbers": [{"ticker": "AAPL", "metric": "Revenue_total", "value": 123000000000, "page": 10}]}

Rules:
- verdict_type "dominant" if one company is ahead on every measure you used, "tradeoff" if not,
  "profile" if the question is about how companies spend money and there is no better or worse (winner null).
- Tickers: AAPL, MSFT, NVDA, AVGO, CSCO.
- Metric names: Revenue_total, GrossProfit, OperatingIncomeLoss, NetIncomeLoss, Assets,
  GrossMargin, OperatingMargin, NetMargin, ReturnOnAssets, ReturnOnEquity, R&D_expense_to_revenue,
  SalesAndMarketing_to_revenue, TotalOperatingExpense_to_revenue.
- Money in US dollars (not millions). Ratios as decimals (0.36, not 36%).
- page = the page number printed at the bottom of the 10-K page the number comes from.

Questions:
Q01: Which is bigger in scale, Apple or Microsoft?
Q02: Which is bigger in scale, Apple or Nvidia?
Q03: Which is bigger in scale, Apple or Broadcom?
Q04: Which is bigger in scale, Apple or Cisco?
Q05: Which is bigger in scale, Microsoft or Nvidia?
Q06: Which is bigger in scale, Microsoft or Broadcom?
Q07: Which is bigger in scale, Microsoft or Cisco?
Q08: Which is bigger in scale, Nvidia or Broadcom?
Q09: Which is bigger in scale, Nvidia or Cisco?
Q10: Which is bigger in scale, Broadcom or Cisco?
Q31: Which company has the largest scale in the IT sector?
```

## Chat 2: Profitability (11 questions)

```
Answer each question below using only the five 10-K reports in this project
(Apple, Microsoft, Nvidia, Broadcom, Cisco; the fiscal 2025 annual reports).
Answer every question on its own; do not reuse numbers you did not look up for that question.

Reply with JSON Lines only: one JSON object per line, one line per question, nothing else.
Each line has this shape:
{"question_id": "Q01", "verdict_type": "dominant" | "tradeoff" | "profile", "winner": "<ticker or null>",
 "answer_text": "<2-3 sentences>",
 "numbers": [{"ticker": "AAPL", "metric": "Revenue_total", "value": 123000000000, "page": 10}]}

Rules:
- verdict_type "dominant" if one company is ahead on every measure you used, "tradeoff" if not,
  "profile" if the question is about how companies spend money and there is no better or worse (winner null).
- Tickers: AAPL, MSFT, NVDA, AVGO, CSCO.
- Metric names: Revenue_total, GrossProfit, OperatingIncomeLoss, NetIncomeLoss, Assets,
  GrossMargin, OperatingMargin, NetMargin, ReturnOnAssets, ReturnOnEquity, R&D_expense_to_revenue,
  SalesAndMarketing_to_revenue, TotalOperatingExpense_to_revenue.
- Money in US dollars (not millions). Ratios as decimals (0.36, not 36%).
- page = the page number printed at the bottom of the 10-K page the number comes from.

Questions:
Q11: Is Apple or Microsoft more profitable?
Q12: Is Apple or Nvidia more profitable?
Q13: Is Apple or Broadcom more profitable?
Q14: Is Apple or Cisco more profitable?
Q15: Is Microsoft or Nvidia more profitable?
Q16: Is Microsoft or Broadcom more profitable?
Q17: Is Microsoft or Cisco more profitable?
Q18: Is Nvidia or Broadcom more profitable?
Q19: Is Nvidia or Cisco more profitable?
Q20: Is Broadcom or Cisco more profitable?
Q32: Which company is the most profitable in the IT sector?
```

## Chat 3: Operating efficiency (11 questions)

```
Answer each question below using only the five 10-K reports in this project
(Apple, Microsoft, Nvidia, Broadcom, Cisco; the fiscal 2025 annual reports).
Answer every question on its own; do not reuse numbers you did not look up for that question.

Reply with JSON Lines only: one JSON object per line, one line per question, nothing else.
Each line has this shape:
{"question_id": "Q01", "verdict_type": "dominant" | "tradeoff" | "profile", "winner": "<ticker or null>",
 "answer_text": "<2-3 sentences>",
 "numbers": [{"ticker": "AAPL", "metric": "Revenue_total", "value": 123000000000, "page": 10}]}

Rules:
- verdict_type "dominant" if one company is ahead on every measure you used, "tradeoff" if not,
  "profile" if the question is about how companies spend money and there is no better or worse (winner null).
- Tickers: AAPL, MSFT, NVDA, AVGO, CSCO.
- Metric names: Revenue_total, GrossProfit, OperatingIncomeLoss, NetIncomeLoss, Assets,
  GrossMargin, OperatingMargin, NetMargin, ReturnOnAssets, ReturnOnEquity, R&D_expense_to_revenue,
  SalesAndMarketing_to_revenue, TotalOperatingExpense_to_revenue.
- Money in US dollars (not millions). Ratios as decimals (0.36, not 36%).
- page = the page number printed at the bottom of the 10-K page the number comes from.

Questions:
Q21: How does the operating efficiency of Apple and Microsoft compare?
Q22: How does the operating efficiency of Apple and Nvidia compare?
Q23: How does the operating efficiency of Apple and Broadcom compare?
Q24: How does the operating efficiency of Apple and Cisco compare?
Q25: How does the operating efficiency of Microsoft and Nvidia compare?
Q26: How does the operating efficiency of Microsoft and Broadcom compare?
Q27: How does the operating efficiency of Microsoft and Cisco compare?
Q28: How does the operating efficiency of Nvidia and Broadcom compare?
Q29: How does the operating efficiency of Nvidia and Cisco compare?
Q30: How does the operating efficiency of Broadcom and Cisco compare?
Q33: How do the five IT companies compare on operating efficiency?
```
