"""
Person D cost test: what one answer costs, our system vs a top AI reading the reports.

    python -m evaluation.cost                 # estimate, no internet or key needed
    python -m evaluation.cost --api           # exact token counts (free count_tokens call, needs ANTHROPIC_API_KEY)
    python -m evaluation.cost --pdf-dir <folder>   # where the five FY2025 10-K PDFs are

Our system sends Claude only the small fact sheet the code built. The top-AI
baseline has to send all five 10-Ks with every question. Prices are the
public per-million-token prices (USD) for Claude Haiku 5.5 and Claude Opus 5.5.

Sources of each number, from best to worst:
  * real:      results/ours.jsonl rows that carry "usage" (run_eval records it)
  * counted:   --api, Anthropic's free token-counting endpoint
  * estimated: characters / 4, a standard rough rule (Haiku 5.5's tokenizer
               counts about 30% more, so its estimate is scaled by 1.3)
The 10-K estimate uses the PDF text only, so it is a lower bound: the API
also reads each PDF page as an image.

Writes results/cost.json.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
PDF_DIR = Path("/mnt/project-files/top_ai_baseline")      # the 5 FY2025 10-Ks given to the top AI; --pdf-dir overrides

# USD per million tokens (input, output). Haiku 5.5: prompts up to 100K tokens.
PRICE = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}
OPUS_CACHE_READ = 0.20          # USD per million input tokens read from the prompt cache
HAIKU_LONG = (0.50, 2.50)       # Haiku 5.5 price when the prompt is over 100K tokens
CONTEXT_WINDOW = 1_000_000      # Opus 5.5 and Haiku 5.5 both read at most 1M tokens at once


def _usd(model: str, tin: float, tout: float) -> float:
    pin, pout = PRICE[model]
    return tin / 1e6 * pin + tout / 1e6 * pout


def our_prompts():
    """The exact system prompt + message our system sends, for every main question."""
    from evaluation.run_eval import load_questions
    from src.analysis import category as cat
    from src.analysis.synthesize import SYSTEM_PROMPT, build_messages
    from src.serve.engine import Engine

    engine = Engine()
    for q in load_questions("main"):
        r = cat.answer(q["question"], engine=engine, llm=None, use_gate=True)
        if "packet" in r:
            yield q["question_id"], SYSTEM_PROMPT, build_messages(q["question"], r["packet"])


def report_pdfs(year: int = 2025, pdf_dir: Path = PDF_DIR):
    return sorted(Path(pdf_dir).glob(f"*_10-K_FY{year}.pdf"))


def main(use_api: bool, pdf_dir: Path = PDF_DIR) -> None:
    client = None
    if use_api:
        import anthropic
        client = anthropic.Anthropic()

    # ---- our system: input tokens per question
    ours_in = []
    for qid, system, msgs in our_prompts():
        if client:
            n = client.messages.count_tokens(model="claude-haiku-5-5", system=system, messages=msgs).input_tokens
        else:
            n = round((len(system) + len(json.dumps(msgs))) / 4 * 1.3)
        ours_in.append(n)
    ours_in_med = statistics.median(ours_in)

    # ---- our system: real usage, if the run recorded it
    real = []
    f = RESULTS / "ours.jsonl"
    if f.exists():
        for line in f.open(encoding="utf-8"):
            u = json.loads(line).get("usage")
            if u and u.get("calls"):
                real.append(u)
    if real:
        ours_tin = statistics.median(u["input_tokens"] for u in real)
        ours_tout = statistics.median(u["output_tokens"] for u in real)
        ours_src = f"real, median of {len(real)} answers in results/ours.jsonl"
    else:
        ours_tin, ours_tout = ours_in_med, None
        ours_src = ("input counted with the API" if client else "input estimated") + \
                   "; output unknown until ours is re-run (run_eval now records it)"

    # ---- top AI: the five FY2025 10-Ks go in with every question
    pdfs = report_pdfs(pdf_dir=pdf_dir)
    if not pdfs:
        raise SystemExit(f"No *_10-K_FY2025.pdf files in {pdf_dir}. Pass --pdf-dir <folder with the 5 PDFs>.")
    if client and pdfs:
        content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                   "data": base64.b64encode(p.read_bytes()).decode()}}
                   for p in pdfs]
        content.append({"type": "text", "text": "Which is bigger, Apple or Microsoft?"})
        top_tin = client.messages.count_tokens(model="claude-opus-5-5",
                                               messages=[{"role": "user", "content": content}]).input_tokens
        top_src = "counted with the API (PDF text and page images)"
    else:
        chars = sum(len(subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True).stdout)
                    for p in pdfs)
        top_tin = round(chars / 4)
        top_src = f"estimated from the text of {len(pdfs)} PDFs (lower bound: page images not counted)"
    top_tout = 500        # a short written answer; the chat answers were a few sentences

    rows = []
    rows.append({"system": "Code only (no AI)", "model": None, "input_tokens": 0, "output_tokens": 0,
                 "usd_per_answer": 0.0, "source": "no AI call"})
    if ours_tout is not None:
        rows.append({"system": "Our system (Haiku 5.5 + fact sheet)", "model": "claude-haiku-5-5",
                     "input_tokens": ours_tin, "output_tokens": ours_tout,
                     "usd_per_answer": round(_usd("claude-haiku-5-5", ours_tin, ours_tout), 5),
                     "source": ours_src})
    else:
        rows.append({"system": "Our system (Haiku 5.5 + fact sheet)", "model": "claude-haiku-5-5",
                     "input_tokens": ours_tin, "output_tokens": None,
                     "usd_per_answer_input_only": round(_usd("claude-haiku-5-5", ours_tin, 0), 5),
                     "source": ours_src})
    rows.append({"system": "Top AI (Opus 5.5) reading 5 reports", "model": "claude-opus-5-5",
                 "input_tokens": top_tin, "output_tokens": top_tout,
                 "usd_per_answer": round(_usd("claude-opus-5-5", top_tin, top_tout), 4),
                 "source": top_src})
    rows.append({"system": "Cheaper AI (Haiku 5.5) reading 5 reports", "model": "claude-haiku-5-5",
                 "input_tokens": top_tin, "output_tokens": top_tout,
                 "usd_per_answer": round(top_tin / 1e6 * HAIKU_LONG[0] + top_tout / 1e6 * HAIKU_LONG[1], 4),
                 "source": top_src + "; Haiku's price for prompts over 100K tokens"})
    rows.append({"system": "Top AI (Opus 5.5), reports kept in the prompt cache", "model": "claude-opus-5-5",
                 "input_tokens": top_tin, "output_tokens": top_tout,
                 "usd_per_answer": round(top_tin / 1e6 * OPUS_CACHE_READ + top_tout / 1e6 * PRICE["claude-opus-5-5"][1], 4),
                 "source": "same tokens, cache-read price; the first question also pays to write the cache"})
    # ---- the many-company question: how much report text would a chat AI need?
    all_pdfs = sorted(Path(pdf_dir).glob("*_10-K_FY*.pdf"))
    per_report = round(sum(len(subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True).stdout)
                           for p in all_pdfs) / 4 / max(1, len(all_pdfs)))
    many = {"avg_tokens_per_10k_estimate": per_report, "from_pdfs": len(all_pdfs),
            "context_window_tokens": CONTEXT_WINDOW,
            "rank_30_companies_2_years_58_reports": per_report * 58,
            "all_180_reports": per_report * 180}
    many["times_the_context_window_58"] = round(many["rank_30_companies_2_years_58_reports"] / CONTEXT_WINDOW, 1)
    many["times_the_context_window_180"] = round(many["all_180_reports"] / CONTEXT_WINDOW, 1)
    print(f"One 10-K is about {per_report:,} tokens. Ranking 30 companies over 2 years needs "
          f"{many['rank_30_companies_2_years_58_reports']:,} tokens = {many['times_the_context_window_58']}x "
          f"the {CONTEXT_WINDOW:,}-token context window of Opus 5.5 / Haiku 5.5.")

    out = {"many_company_question": many, "prices_usd_per_million_tokens": PRICE, "opus_cache_read": OPUS_CACHE_READ,
           "our_prompt_tokens_per_question": ours_in, "rows": rows}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "cost.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    for r in rows:
        usd = r.get("usd_per_answer", r.get("usd_per_answer_input_only"))
        print(f"{r['system']:<55} in={r['input_tokens']!s:>9} out={r['output_tokens']!s:>6}  ${usd}  ({r['source']})")
    print("Saved -> evaluation/results/cost.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", action="store_true", help="count tokens exactly (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--pdf-dir", default=str(PDF_DIR), help="folder with the five *_10-K_FY2025.pdf files")
    a = ap.parse_args()
    main(a.api, Path(a.pdf_dir))
