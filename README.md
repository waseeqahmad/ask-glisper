# Ask Glisper

**An agent that tells a small brand why AI engines recommend a rival instead of them, proves every claim with SQL,
and gives one thing to fix this week.**

Open Agent Hackathon 2026 · Track: Wildcard [Tinkerer] (extends Glisper, an existing AI-search visibility product)

## For judges: see it, then run it (2 minutes, no keys)

- **Demo video:** VIDEO_LINK
- **Slides:** SLIDES_LINK
- **Saved runs:** `output_examples/live-*.json` (real Zetaris + Nightshift/Nemotron runs, with traces) and `sample-*.json`

Needs Python 3.11+ (nothing to pip install) and Node 20+ (for the H-MEM memory engine; without Node the agent still
runs and logs that memory was skipped).

```bash
git clone https://github.com/waseeqahmad/ask-glisper && cd ask-glisper
cd engines/hmem && npm install && cd ../..
SAMPLE_MODE=true python3 -m glisper_agent.server       # open http://127.0.0.1:8090
```

Windows (PowerShell): `$env:SAMPLE_MODE="true"; python -m glisper_agent.server`

SAMPLE_MODE needs no Zetaris, no Nightshift and no keys. The agents still query the demo data (sqlite, the same SQL),
and the Evaluator still scores every draft. Things to try:

1. Ask **"Why are we losing on Perplexity?"**, then switch to **Detailed** to see each agent step, the SQL, the rows
   and the Evaluator's 0–9 score.
2. Open **Glisper remembers** (bottom left) and add **"Ignore Kurta Co: they only ship in Karachi"**. H-MEM stores it.
   Then ask **"Who beats us on Gemini?"**: the trace shows the fact recalled, the answer leaves Kurta Co out, and the
   Evaluator rejects drafts that mention it.
3. Ask a follow-up in the same chat: **"What about ChatGPT?"** keeps the context and switches engine. (Free-text
   follow-ups like "draft the outreach email" need the live models, so they appear in the video, not in SAMPLE_MODE.)

Tests: `python3 -m unittest discover tests` (12 tests) and `cd engines/hmem && npm test` (H-MEM conformance 13/13 +
cross-session recall). The live mode (Zetaris + Nightshift + NVIDIA Nemotron) is under [Run it](#run-it) and shown in
the video.

- **User:** the marketing lead at a small business or agency that tracks its AI-search visibility in Glisper.
- **Problem:** Glisper shows *that* ChatGPT, Perplexity, Gemini and Claude recommend a rival. It doesn't show *why*, so the
  user guesses, or reads hundreds of answers by hand.
- **Decision improved:** which fix to do this week.

Ask "Why are we losing on Perplexity?" The agent:
1. recalls what it knows about the brand (H-MEM),
2. plans which analyses to run,
3. writes SQL with NVIDIA Nemotron and runs it through Zetaris,
4. drafts an answer, which an Evaluator checks against the rows. The Evaluator rejects invented numbers, causal
   claims and pointless actions, and sends a critique back for a rewrite.
5. returns the answer, an evidence table, real answer quotes, a confidence level and one action.

## Agent loop

```
question ─▶ Memory (H-MEM recall: brand facts from earlier sessions)
         ─▶ Planner (Nemotron)  routes to 1–2 skills: competitor_gap, source_gap · engine · brands to ignore
         ─▶ Analyst (Nemotron)  writes SQL ─▶ run_sql (SELECT-only guard) ─▶ Zetaris
               ▲                                │
               └──── Evaluator: reject query ◀──┘  (SQL error, empty, or no won-vs-lost contrast → retry, then template)
         ─▶ Reporter (Nemotron) draft ─▶ Evaluator rubric 0–3 each:
               grounding     every number must appear in a query result
               honesty       no volume/traffic claims, no "one fix solves it", no causal wording for co-citation,
                             no rival's own site as a "gap", obey brand memory (ignored rivals)
               actionability one action naming a third-party domain from the rows (+ Nemotron specificity check)
            < 7/9 → critique back to the Reporter (≤ 2 rewrites) · still failing → shown as "low confidence"
         ─▶ get_answer: quotes from real answers that cite the action's target ─▶ final answer
```

Guardrails: ≤ 6 SQL calls per run, 30 s per query, 200-row cap, 420 s per run, SELECT-only on three tables, H-MEM writes
only after the user clicks **Approve**. Every step is written to `logs/<run_id>.jsonl` (agent, decision, result, next,
model, SQL, rows) and streamed live to the trace panel.

## Sponsor tech: what each piece does

| | Job in the product | Where |
|---|---|---|
| **Zetaris** | Data layer. The audit lives in Postgres, registered in Zetaris as `GLISPER_DEMO`; every agent query is SQL through the Zetaris query API | `glisper_agent/backends.py: run_sql`, `scripts/register_zetaris.py` |
| **Meterless H-MEM** | Brand memory across sessions ("Ignore Kurta Co: they only ship in Karachi"). Recalled at the start of every run; the Planner turns it into exclusions; the Evaluator rejects answers that break it | `engines/hmem/` (reference engine + `hmem_cli.ts`), `backends.py: remember/recall` |
| **Meterless Nightshift** | Every model call. Planner, Analyst, Reporter and Evaluator call Nightshift's OpenAI-compatible API | `backends.py: chat` |
| **NVIDIA Nemotron** (via Nightshift) | `nemotron-3-ultra-550b-a55b` writes the SQL and the answer; `nemotron-3.5-lightning-30b-a3b` plans and judges specificity (falls back to Ultra on errors). The model of every step is in the trace and the log | `backends.py: MODEL, FAST_MODEL` |

H-MEM test: `cd engines/hmem && npm test`. This runs the H-MEM conformance suite (13/13) and our cross-session recall test
(remember in one process, recall in a new one).

## Run it

Needs Python 3.11+ (stdlib only) and Node 20+.

```bash
cd engines/hmem && npm install && cd ../..        # H-MEM engine (tsx is the only dependency)
SAMPLE_MODE=true python3 -m glisper_agent.server  # http://127.0.0.1:8090, no keys, no services
```

`SAMPLE_MODE=true` swaps Zetaris for sqlite over `data/sample/*.csv` (the same SQL), and swaps the model calls for
rule-based steps that still read the query rows. H-MEM is the real engine in both modes.

The chat page keeps every conversation on the left; open one to continue it, and follow-ups ("what about Gemini?",
"draft the message") build on the earlier turns. **Simple** view is the end-user answer (what to do this week, one
example answer, fact-checked or not); **Detailed** adds the live agent trace, Evaluator score, retries, models, SQL and
query rows. `?view=simple|detailed` picks one. The same page is what the Glisper panel embeds.

Full mode (Zetaris + Nightshift):

```bash
cp .env.example .env                                   # fill NIGHTSHIFT_API_KEY, GLISPER_DEMO_DB_URL
python3 scripts/make_demo_data.py --load "$GLISPER_DEMO_DB_URL"   # needs psql
python3 scripts/register_zetaris.py                    # registers the source in Zetaris (Docker, localhost:3001)
python3 -m glisper_agent.server                        # or the CLI:
python3 -m glisper_agent.agent "Why are we losing on Perplexity?"
python3 -m glisper_agent.agent --remember "Ignore Kurta Co: they only ship in Karachi"
```

### Inside Glisper (real customer flow)

The agent can answer for any brand in a Glisper database. Glisper's tables stay untouched: three read-only views
(`scripts/glisper_views.sql`, schema `ask_glisper`) are registered in Zetaris as `GLISPER`, and the agent reads them
as a role that can see nothing else. `run_sql()` pins every query to the signed-in brand's audit in code (the
tables are shadowed by CTEs filtered on `audit_id`, and catalog names or qualified tables are rejected), so one
brand's question can never read another brand's rows.

```bash
psql "$GLISPER_ADMIN_DB_URL" -v ro_password="..." -f scripts/glisper_views.sql   # views + read-only role
python3 scripts/register_zetaris.py GLISPER_DB_URL GLISPER                      # register the views in Zetaris
GLISPER_ADMIN_DB_URL=... python3 scripts/load_demo_into_glisper.py you@example.com   # optional: demo brand as a workspace
python3 -m glisper_agent.agent --brand <glisper brand id> "Why are we losing on Perplexity?"
```

In Glisper, the "Ask Glisper" button in the top bar opens this agent for the signed-in workspace's brand
(`?brand=<id>`). Answers carry a data label from the audit's capture methods (synthetic, dev-mode, or real).

`./stack.sh up|down|status` starts and stops Zetaris (Docker) and the Nightshift app on a Mac.

Tests (no keys needed): `python3 -m unittest discover tests` (SQL guard, Evaluator rubric, the full loop in SAMPLE_MODE)
and `cd engines/hmem && npm test`.

Examples: `input_examples/questions.txt` and the saved runs in `output_examples/`.

## Data

A synthetic demo audit of a fictional brand, with one planted pattern. Every answer is labelled "Simulated data".
See [data/README.md](data/README.md).

## What's new vs pre-existing

Pre-existing (not in this repo): **Glisper**, our AI-search visibility product (engine runs, answer scoring, its database).
New in the build window (everything in this repo): the agent loop, Evaluator, H-MEM memory, Zetaris integration,
Nightshift/Nemotron routing, demo data generator, chat page and tests. The agent reads Glisper only through SQL.

## Limitations and next steps

- Demo data is synthetic; next step is real audits through read-only Glisper views.
- Two skills today (competitor gap, source gap). Next: noise check across weekly runs, engine comparison, one prompt's answers.
- Inside Glisper: a chat panel with "Why?" buttons on lost rows, memory in Glisper's own database.
