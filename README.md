# Ledger Pilot

AI ledger classification for Indian CA firms. A bank statement goes in; every line
is classified to **that client's own Tally ledger** with a confidence score and its
evidence; a person reviews only the uncertain lines; vouchers post into Tally under
one idempotent reference; every correction teaches that client's memory.

**Repository:** <https://github.com/fwddeploy/ca> — branch `main`.

---

## Read these, in this order

| # | File | What it is |
|---|---|---|
| 1 | **This README** | Setup, commands, where it stands, what to do next |
| 2 | [`docs/STATE.md`](docs/STATE.md) | The position log — what was done, in order, with evidence |
| 3 | [`docs/prd.md`](docs/prd.md) | What to build, and what is deliberately out of v1 |
| 4 | [`docs/trd.md`](docs/trd.md) | How — stack, data model, engine design, the go/no-go bars |
| 5 | [`docs/decisions.md`](docs/decisions.md) | Research conclusions. **Do not relitigate casually** |
| 6 | [`CLAUDE.md`](CLAUDE.md) | The nine rules that must not be broken. Read before changing anything |

`AGENTS.md` is a copy of `CLAUDE.md` for non-Claude agents. Keep them in sync.

---

## Setup

Verified from a clean `git clone` on Windows with Python 3.12.10 — 20/20 tests
pass with and without Docker.

```bash
git clone https://github.com/fwddeploy/ca.git
cd ca
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

That is enough to run everything. **Docker is optional** — with no `DATABASE_URL`
set the app uses a local SQLite file, so a fresh clone runs with nothing else
installed.

For Postgres (what production uses):

```bash
docker compose up -d
set DATABASE_URL=postgresql+psycopg://ledger:ledger@localhost:5433/ledger_pilot
```

Port **5433**, not 5432 — deliberately, so Ledger Pilot never collides with
another project's database.

## Commands

```bash
.venv\Scripts\python -m pytest tests\test_all.py -q       # 20 tests, must stay green
.venv\Scripts\python -m harness.run                       # must end "ALL CHECKS PASS"
.venv\Scripts\python -m harness.run --materiality 50000   # currently FAILS — see below
.venv\Scripts\python -m uvicorn api.main:app --port 8000  # the app, at localhost:8000
```

To run the whole suite against Postgres instead of SQLite:

```bash
set LP_TEST_DB_URL=postgresql+psycopg://ledger:ledger@localhost:5433/ledger_pilot_test
```

The suite always creates its own throwaway database and refuses any database whose
name does not contain `test`. It posts and undoes freely; one day `DATABASE_URL`
will point at a real firm's books.

---

## See it without installing anything

Two published pages (private — share them from each page's share menu):

- **[Working demo](https://claude.ai/code/artifact/29d785df-dbde-4f26-a95b-b1a3573acc47)** — the real app with real engine output baked in. Click a client, review the queue, post to Tally, undo. Upload shows the balance gate on a clean file and an edited one.
- **[How it works](https://claude.ai/code/artifact/91af2c61-4769-4d71-a693-67be62d956c7)** — the flow explained in plain words, following one ₹12,000 payment from bank line to Tally entry. Hover any ⓘ for the exact rule.

---

## Layout

```
engine/     the brain
  parsers/    per-bank YAML column configs + the balance-continuity check
  enrich.py   VPA / UTR / GSTIN / PAN / cheque extraction, channel, counterparty key
  taxonomy.*  ~45 glossed Indian chart-of-accounts categories
  memory.py   T0 hard rules + T1 learned patterns, keyed on Tally GUIDs
  tiers.py    the T0 → T1 → T2 → T3 cascade
  llm.py      MockLLM (offline, deterministic) / AnthropicLLM (production)
  calibrate.py per-client confidence threshold + materiality guardrails
  pipeline.py parse → enrich → classify → route, the whole engine in one call
bridge/     Tally voucher / masters / undo XML, idempotent LP-{batch}-{seq} refs
db/         models.py (TRD §4 schema) · session.py · store.py — the only place SQL lives
api/        main.py (FastAPI over db/store.py) · static/index.html (the SPA)
harness/    synthetic golden data + run.py, the accuracy gate
tests/      test_all.py — 20 tests
```

---

## Where it actually stands

Everything below is **E3 at best**: the code runs, tests cover it, and the tests
were proven to fail without their fix. It is **not** evidence of real-world
accuracy, because every number comes from synthetic data.

| Area | State | Evidence |
|---|---|---|
| Engine, calibration, Tally XML | works | 8 tests, harness green |
| Two-step upload + balance gate | works | 4 tests, mutation-checked, verified in a live browser |
| Posting idempotency, undo, pure reads | works | 3 tests, mutation-checked |
| Postgres / SQLite persistence | works | 20/20 on **both** backends |
| Learning survives a restart | works | 1 test: correct a cold client, reopen the DB, next month it remembers |
| Real Tier-3 LLM | **not built** | `AnthropicLLM` exists but is unbatched and unrun |
| Auth, roles, maker-checker | **not built** | — |
| PDF ingestion | **not built** | CSV/XLSX only |
| Accuracy on real books | **unknown** | nothing has ever been run on real data |

### The numbers

49-line synthetic test month, warm client with six months of history. Suggestion
accuracy is **98.0% top-1 at every setting** — only the routing changes.

| Materiality | Queued on size alone | Auto-classified | Precision | Gate |
|---|---|---|---|---|
| ₹2,00,000 (harness default) | 1 / 49 | 87.8% | 100% | passes |
| ₹1,00,000 | 9 / 49 | 77.6% | 100% | passes |
| **₹50,000 (what ships)** | **17 / 49** | **59.2%** | 100% | **FAILS** |

Cold start (no history at all): 73.5% top-1, 0% auto by design. Zero dangerous
auto-posts in every run.

### The open decision — needs the owner, not an engineer

`python -m harness.run --materiality 50000` **fails** the TRD §10 check
"warm auto ≥ 75%", and **cannot be made to pass**. 17 of 49 lines sit above
₹50,000, so the guardrail alone caps the auto-rate at 63.3%.

Of the 20 lines that queue at ₹50,000: 17 for size, 2 for being a first-seen
supplier, 1 with no suggestion — and **none because the classifier was unsure**.
19 of those 20 already carry the correct ledger. The reviewer is confirming, not
correcting.

So the 75% bar cannot be reached by improving the engine. Three options, and only
real data can choose between them:

1. Raise the default to ₹1,00,000 — passes, but this is "loosen the guardrail to
   raise the auto-rate", which principle 1 forbids without justification.
2. Make materiality relative to the client's own turnover. Defensible — materiality
   in audit *is* entity-relative.
3. Keep the guardrail and make confirming a large line cost one keystroke instead
   of a decision. Doesn't move the auto-rate; does move the metric that matters.

**The check was left failing rather than weakened.** Do not "fix" it by editing
the bar.

---

## Traps that will bite you

- **Never weaken a harness check to make it pass.** If a check regresses, the
  change is wrong. Changing a bar needs the owner's explicit sign-off.
- **One statement, one batch id, forever.** A GET must never post, and a second
  `POST /post` must return the first batch. Two batch ids means Tally imports every
  line twice and undo can only cancel one. This was a live defect; tests pin it now.
- **A correction may propose a rule. It must never rewrite one.**
- **Ledger GUIDs are the keys, never names.** Names are display only.
- **Never quote an auto-rate without its materiality.** It is not a measurement
  otherwise.
- **The suite wipes its database.** It refuses anything not named `*test*`, but
  do not disable that guard.
- Windows consoles are cp1252; `harness/run.py` reconfigures stdout to UTF-8 or it
  dies printing ₹.

## Never, under any circumstances

- Real client data in git — narrations, party names, GSTINs, amounts — including
  in commit messages. `.gitignore` blocks `data/`, `*.db`, `*.xml`, `*.env`.
- Secrets in git, chat, or logs.
- Synthetic data used to claim accuracy, a match rate, or a threshold.
- A claim without a reproduction.

---

## What to do first

1. Run the setup above and confirm 20/20 and `ALL CHECKS PASS`. If either fails,
   stop and fix that before anything else.
2. Read `docs/decisions.md`. It will save you from re-deriving conclusions that
   already cost a research sweep.
3. **Get real data.** One design partner's Tally voucher export, their ledger
   masters, and the matching bank statements. Wire them into `harness/` as a golden
   set. Nothing else moves any claim above E3, and that one run answers the
   materiality question above better than any amount of building.

Then, roughly in order: statement-upload polish · auth (email OTP) and roles ·
maker-checker release · rules & memory screen · automation scoreboard · pdfplumber
ingestion and real bank configs beyond the four stubs · batched `AnthropicLLM`
Tier 3.
