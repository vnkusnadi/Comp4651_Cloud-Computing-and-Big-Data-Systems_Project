# Evaluation (Person D)

This folder tests whether SectorView gives better answers than simpler systems.
It also holds the AI verdict step's tests. The AI verdict code itself is in `src/analysis/`.

## What is here

| File | What it does | Needs |
|---|---|---|
| `questions.csv` | 33 test questions (10 company pairs x 3 question types, plus 3 industry-wide), 6 in everyday wording, and 24 blind everyday questions (N01-N24) | nothing |
| `answer_key_values.csv` | The true numbers and their printed pages, read by AI from the 10-K reports (not from our data); spot-check by hand | done, spot-check |
| `answer_key_verdicts.csv` | The right answer to each question, from those numbers with one stated rule | done |
| `run_eval.py` | Runs every question through one system and saves the answers in `results/` | nothing, or an API key for `ours` |
| `score.py` | Scores saved answers against your answer key (the 6 measures on slide 19) | the answer key |
| `growth_eval.py`, `answer_key_growth.csv`, `top_ai_growth_prompt.md` | Multi-year test: 10 growth questions (fiscal 2021 to 2025), each needing two 10-Ks per company; our code and AI baselines | nothing for our code |
| `data_checks.py` | Checks the saved numbers agree with each other (slide 20) | nothing |
| `ablation_division_check.py` | Switch-off test: turn off the division check and count what breaks | nothing |
| `speed_test.py` | Speed test: answering speed, and the data pipeline at 15+ reports | internet access to sec.gov for the pipeline part |
| `cost.py` | Cost per answer: our system (Haiku 5.5 reading the small fact sheet) vs Opus or Haiku reading the full 10-Ks; also how many tokens the 30-company question would need | nothing (estimate), or an API key for exact counts |
| `spark_vs_python.py` | Person B's step on Spark (1 and 4 cores) vs plain Python, at 862, 26,293 and 262,930 numbers | Java + pyspark |
| `../src/analysis/sector_rank.py` | Many-company question: rank all 30 IT companies by growth between two years, code only | the 30-company data |
| `flat_rag.py` | Baseline: plain AI search over the 10-K text | the 10-K files and an API key |
| `top_ai_instructions.md`, `top_ai_prompts.md`, `top_ai_import.py` | Baseline: a top AI model reading the 10-Ks in claude.ai (about 30 minutes by hand) | you |

## The systems we compare

| Name in results | What it is |
|---|---|
| `code_only` | Fact sheet plus a fixed equal-weight score. No AI. |
| `ours` | Fact sheet, then Claude writes the verdict, then the 7 checks. If the checks fail twice, the code-only answer is shown. |
| `ours_no_gate` | Same, but the 7 checks are switched off (switch-off test). |
| `flat_rag` | Plain AI search over the report text. |
| `top_ai` | The strongest Claude (Opus) reading the full 10-Ks with no help from our system. |
| `weak_ai` | The cheapest Claude (Haiku) reading the full 10-Ks with no help from our system. |

## How to run (from the repository root)

```bash
pip install -r requirements.txt anthropic
python -m pytest -q                                   # all tests, should all pass

python -m evaluation.data_checks                      # slide 20 numbers
python -m evaluation.ablation_division_check          # switch-off test 1
python -m evaluation.speed_test query                 # answering speed
SEC_USER_AGENT="Name email" python -m evaluation.speed_test ingest   # 15 reports, 1 vs 5 processes
SEC_USER_AGENT="Name email" python -m evaluation.speed_test ingest --years 2020 2021 2022 2023 2024 2025 \
    --workers 1 4 --tickers AAPL MSFT NVDA AVGO CSCO ORCL CRM ADBE AMD ACN IBM INTU QCOM TXN AMAT NOW MU LRCX ADI KLAC PANW SNPS CDNS ANET INTC FTNT MSI APH ADSK HPQ
python -m evaluation.spark_vs_python                 # Spark vs plain Python
python -m src.analysis.sector_rank --from 2020 --to 2025   # rank all 30 companies (data/evidence_units_it30)
python -m evaluation.cost                            # cost per answer (add --api for exact token counts)

python -m evaluation.run_eval --system code_only
export ANTHROPIC_API_KEY=sk-ant-...                   # see "Get an API key" below
python -m evaluation.run_eval --system ours
python -m evaluation.run_eval --system ours_no_gate
python -m evaluation.run_eval --system ours --set everyday_wording
python -m evaluation.run_eval --system code_only --set blind_everyday

python -m evaluation.score                            # scores table for every results file
python -m evaluation.growth_eval                      # multi-year test (uses data/evidence_units_5y, 25 reports)
```

## Rules that keep the test fair

1. The answer key values were read by AI (Claude Opus 5.5) directly from the five 10-K filings on SEC EDGAR, never copied from our own data. Page = the number printed at the foot of the page. Say this in the report, and spot-check at least 12 values by hand (one per metric) against the PDFs.
2. Expected verdicts follow one stated rule: if one company is higher on every scored measure, it is the only right winner ("dominant"); otherwise any company not beaten on every measure is accepted ("tradeoff"). Spending ratios are a profile with no winner.
3. A right answer built from a wrong number or a wrong page still fails `strict_accuracy`.
4. Margins and ratios are recomputed from the key values with the same formula.
5. If our system loses to a baseline, we report it.

## Honest notes

- Because the verdict rule (rule 2) matches how our system decides, `code_only` scores 100% on answer accuracy almost by design. The meaningful comparisons are strict accuracy, numbers, sources, and the baselines.
- Code-only results against the key (33 questions), after the fixes below: answer 100%, strict 100%, numbers 100%, right page 100%. Before the fixes, strict was 78.8% and numbers 98.2%. Two numbers disagreed with the key:
  - Apple selling and marketing: the key was wrong, not our system. Apple prints selling and marketing (19,524) and G&A (8,077) separately in Note 13 (page 47); the first key used the combined SG&A line. Key corrected.
  - Cisco EBITDA margin: our system was wrong. It used a 700 million "depreciation of property and equipment" figure from a sentence in the notes, which leaves out amortization. Fix: depreciation now only counts when it comes from a row of a financial statement, so Cisco's EBITDA margin is reported as missing instead of too low.
  Both were found by this test and then fixed, so the main 33 questions are no longer an unseen test for these two numbers.
- The key found a real bug: our cited pages were 1 to 2 pages off for Apple, Broadcom and Cisco (we counted pages from the cover instead of using the printed number). Fixed in Person A's pipeline; right-page score went from 40% to 100%.
- The 6 everyday-wording questions (P01 to P06) were used to improve the question reader, so they are a weak test. The 24 blind questions (N01 to N24) were written by a separate AI that never saw the question reader: 16 of 24 (67%) were read correctly. Failures are mostly "X as a share of revenue" and plain words for spending ("inventing new things", "advertising and offices"). See `results/blind_everyday_grading.csv`; the grading was done by AI and should be checked.
- Baselines (24 Nov, claude.ai, 5 full 10-K PDFs per chat, 11 questions per chat): Opus and Haiku both got 100% of Scale and Profitability answers right, with 100% of numbers and printed pages right. Both lost only on the 11 spending questions, where they named a winner and our key says "profile, no winner": a difference in definition, not a factual error. They took about 12 s (Opus) and 8 s (Haiku) per answer against about 0.06 s for our code. claude.ai ran code on the PDFs in these chats. Honest reading: our system is not more accurate than a top AI on these questions; its edge is speed, cost per answer, the same answer every time, and a source for every number.
- An earlier version of the scoring marked "tradeoff, no single winner" as wrong. That was changed after seeing the baseline answers, because it is a fair answer; the change helps the baselines, not us.
- Our system with a real AI (Claude Haiku 5.5, re-run 28 Nov on Rey's computer): 33/33 answers, strict 100%, numbers and pages 100%, 100% of claims with sources, about 11.6 s per answer (about 14.8 s with the checks off; the gap is API speed, not the checks). Every answer passed the 7 checks on the first try (0 fallbacks), and with the checks off (`ours_no_gate`) the scores were the same apart from 99.5% of claims with sources, so on this test the checks never had to act. The AI adds a written explanation, not accuracy: `code_only` already scores 33/33 in 0.05 s.
- `run_eval` saves the tokens of every Claude call (`usage` in each row), so `cost.py` prices our system from real numbers: one Claude call per answer, median 4,060 tokens in and 2,824 out.

## Scale-up (28 Nov): 30 IT companies, fiscal 2020 to 2025

- Data: 30 US-listed IT companies x 6 years = 180 10-Ks; 179 loaded (Cadence fiscal 2020 ended on 2 January 2021, so our "year of the period end" rule files it under 2021), 26,293 saved numbers, 20 MB. Stored in `data_ingestion_and_processing/data/evidence_units_it30/`. IBM files its financial statements as an exhibit, so IBM numbers have no page links (31 numbers per year instead of about 150).
- Speed of the data step (4-core cloud machine, includes downloading from sec.gov):

  | Reports | Numbers | One at a time | 4 in parallel | Speed-up |
  |---|---|---|---|---|
  | 5 | 829 | 15.1 s | 6.4 s | 2.4x |
  | 30 | 4,578 | 111.7 s | 23.9 s | 4.7x |
  | 90 | 13,216 | 249.8 s | 92.6 s | 2.7x |
  | 179 | 26,293 | 523.1 s | 134.2 s | 3.9x |

  Time grows in a straight line with the number of reports (about 2.9 s per report one at a time, 0.75 s with 4 in parallel).
- Spark vs plain Python (same functions, Person B's step): 862 numbers: Python 0.01 s, Spark 1 core 2.3 s, 4 cores 1.8 s. 26,293 numbers: 0.63 s, 3.1 s, 2.5 s. 262,930 numbers (the 30-company data copied 10 times): 7.3 s, 18.8 s, 10.5 s. Honest reading: plain Python wins at every size we can reach; Spark gets faster with more cores (1.8x from 1 to 4) and would only win with far more data spread over several machines.
- Many-company question, "rank all 30 IT companies by revenue growth from 2020 to 2025": code only answers from 58 reports in 2.5 s (plus 5 s to load the data), each number with its page. 29 of 30 ranked (Cadence 2020 missing). Nvidia first (+1,095%), Intel last (-32%). A chat AI would need about 4.8 million tokens of report text, 4.8 times the 1 million-token window of Opus 5.5 and Haiku 5.5 (estimate: 10-K text / 4), so it cannot read them all at once.
- Cost per answer (`cost.py`, 28 Nov): our system (Haiku 5.5 reading the fact sheet) about $0.0018, from real token counts of 33 answers; Opus 5.5 reading the five 2025 10-Ks about $1.65 (about $0.09 if the reports stay in the prompt cache); Haiku 5.5 reading them about $0.21; code only $0. So our system is about 900x cheaper than Opus reading the reports, about 110x cheaper than Haiku reading them and about 50x cheaper than cached Opus. The 10-K figures leave out page images and assume a short 500-token answer, so they are lower bounds. Code only is free and just as accurate, so the AI step is the only cost.
