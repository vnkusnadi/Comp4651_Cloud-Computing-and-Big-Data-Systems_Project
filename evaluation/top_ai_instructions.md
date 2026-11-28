# Baselines: a strong and a weak AI model reading the 10-Ks (done by hand, about 30 minutes each)

This tests the hardest question a marker will ask: "Why not just ask Claude?"

## Steps

1. In claude.ai, create a new **Project** (left sidebar, Projects, New project). Name it "SectorView top-AI baseline".
2. Add the five 10-K PDFs to the project's files. They are in the project folder `top_ai_baseline/`
   (made from the SEC filings, one PDF per company). Don't add anything else: no fact sheet, no notes.
3. Pick the strongest model in the model menu (Opus).
4. Open `evaluation/top_ai_prompts.md`. It has 3 ready prompts (Scale 11, Profitability 11, Operating efficiency 11 questions).
   For each one: start a **new chat inside the project**, paste the prompt, start a stopwatch, and stop it when the answer is complete.
5. Copy every answer line (the lines starting with `{`) from all 3 chats into `evaluation/top_ai_raw.txt`.
6. Run `python -m evaluation.top_ai_import --seconds <chat1> <chat2> <chat3>` with your 3 stopwatch times.
7. Run `python -m evaluation.score`. The `top_ai` row is the baseline.

Then repeat steps 4 to 6 with the cheapest model (Haiku) selected, pasting into `evaluation/weak_ai_raw.txt`
and running `python -m evaluation.top_ai_import --seconds <chat1> <chat2> <chat3> --system weak_ai`.

Why both: `ours` runs on Haiku. If Haiku plus our system matches Opus reading the raw 10-Ks, and beats Haiku
reading them alone, the project has shown its point: a cheap model with checked, sourced numbers.

If an answer is cut off, type "continue" in the same chat; if a question is missing, the import tells you which.

## Fairness notes for the report

- The top AI gets the full 10-Ks, the same metric names, and the same output format as our system.
- It answers 11 questions per chat instead of 1, which saves time but lets it reuse numbers within a chat.
  This helps the baseline, so if our system still wins, the result is conservative.
- Its time per answer is the chat's time divided by 11, so it is an estimate.
