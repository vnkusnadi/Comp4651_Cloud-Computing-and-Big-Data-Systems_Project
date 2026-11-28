# Multi-year test: the prompt to paste

One new chat. Attach all 10 PDFs (5 for fiscal 2021, 5 for fiscal 2025), paste the block below, press Enter and start the stopwatch.

```
Answer each question below using only the ten 10-K reports attached
(Apple, Microsoft, Nvidia, Broadcom, Cisco; each company's fiscal 2021 and fiscal 2025 annual reports).

Reply with JSON Lines only: one JSON object per line, one line per question, nothing else.
Each line has this shape:
{"question_id": "G01", "winner": "<ticker>", "answer_text": "<1-2 sentences>",
 "numbers": [{"ticker": "AAPL", "metric": "Revenue_total", "fiscal_year": 2021, "value": 123000000000, "page": 10}]}

Rules:
- Growth = fiscal 2025 value / fiscal 2021 value - 1. The winner is the company with the higher growth.
- List the fiscal 2021 and fiscal 2025 value for both companies (4 numbers per question).
- Tickers: AAPL, MSFT, NVDA, AVGO, CSCO. Metric names: Revenue_total, NetIncomeLoss.
- Money in US dollars (not millions). page = the page number printed at the bottom of the 10-K page the number comes from.

Questions:
G01: Which grew revenue faster from fiscal 2021 to fiscal 2025, Apple or Microsoft?
G02: Which grew revenue faster from fiscal 2021 to fiscal 2025, Nvidia or Broadcom?
G03: Which grew revenue faster from fiscal 2021 to fiscal 2025, Cisco or Apple?
G04: Which grew revenue faster from fiscal 2021 to fiscal 2025, Microsoft or Nvidia?
G05: Which grew revenue faster from fiscal 2021 to fiscal 2025, Broadcom or Cisco?
G06: Which grew net income faster from fiscal 2021 to fiscal 2025, Apple or Nvidia?
G07: Which grew net income faster from fiscal 2021 to fiscal 2025, Microsoft or Cisco?
G08: Which grew net income faster from fiscal 2021 to fiscal 2025, Broadcom or Apple?
G09: Which grew net income faster from fiscal 2021 to fiscal 2025, Nvidia or Cisco?
G10: Which grew net income faster from fiscal 2021 to fiscal 2025, Microsoft or Broadcom?
```
