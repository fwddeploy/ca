# Ledger Pilot — agent instructions

AI ledger classification for Indian CA firms. Bank statement in → each line
classified to the client's own Tally ledger with a confidence score → human
reviews only the uncertain lines → vouchers posted into Tally → every
correction teaches that client's memory.

Read `docs/prd.md` (what to build) and `docs/trd.md` (how) before large changes.
`docs/decisions.md` holds hard-won research decisions — do not relitigate them
casually.

## Layout

- `engine/` — the product's brain. `parsers/` (bank YAML configs + balance
  check), `enrich.py` (VPA/UTR/GSTIN extraction, channel, counterparty key),
  `taxonomy.yaml` + `taxonomy.py`, `memory.py` (rules + learned patterns),
  `tiers.py` (T0 rules → T1 memory → T2 similarity → T3 LLM), `llm.py`
  (MockLLM offline / AnthropicLLM prod), `calibrate.py`, `pipeline.py`.
- `bridge/tally_xml.py` — voucher/masters/undo XML, idempotent `LP-{batch}-{seq}` refs.
- `harness/` — synthetic golden data + `run.py`, the accuracy gate.
- `api/main.py` — FastAPI (in-memory demo store); `api/static/index.html` — the SPA.
- `tests/test_all.py` — 12 tests (8 engine, 4 posting-safety).

## Commands

```
.venv\Scripts\python tests\test_all.py                  # must stay green
.venv\Scripts\python -m harness.run                     # must end "ALL CHECKS PASS"
.venv\Scripts\python -m harness.run --materiality 50000 # currently FAILS — see README
.venv\Scripts\uvicorn api.main:app --port 8000          # app at http://localhost:8000
```

Setup: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`.

## Non-negotiable principles

1. **Precision beats coverage.** Never loosen thresholds or guardrails to raise
   the auto-rate. The trust number is precision on auto-approved lines (≥98%).
2. **Rule stability.** A correction updates pattern counts and may PROPOSE a
   rule; it never silently rewrites an existing rule. (memory.py enforces this.)
3. **Ledger GUIDs, never names,** as memory/storage keys. Names are display only.
4. **Deterministic before ML.** Same narration → same ledger, always. Recurring
   lines must hit T0/T1, never the LLM.
5. **Abstain on split history** and on materiality (big amounts queue even at
   high confidence). Silent wrong posts into Tally are the product-killing bug.
6. **The harness is the gate.** Any engine change runs `harness/run.py`; if a
   check regresses, the change is wrong or the check's update needs explicit
   human sign-off. Never weaken a check to make it pass.
7. **Privacy:** LLM calls send single narration lines + the client's ledger
   names only — never files, balances, or the firm's client list. No client
   data in logs.
8. **One statement, one batch id, forever. Reads never write.** A GET must not
   post, and a second POST /post must return the first batch, not a new one.
   Two batch ids for one statement puts every line into Tally twice under two
   `LP-` refs, and `undo_xml` — keyed on the batch — can only cancel one of
   them. Both of those were live defects; `tests/test_all.py` now pins them.
9. **State the guardrail with the number.** An auto-rate quoted without its
   materiality is not a measurement. `harness/run.py` prints its value and
   takes `--materiality`; keep it that way.

## Current state / next work (M1 → M2)

Done: engine, calibration, harness, Tally XML, FastAPI demo API, working SPA.
Reproduced on a clean machine (12/12 tests, harness green at ₹2,00,000).
Posting is now idempotent, `GET /xml` no longer writes, `/undo` is exposed.

**Open, needs the owner:** the go/no-go bar "warm auto ≥ 75%" cannot be met at
the shipped ₹50,000 materiality — the guardrail alone caps it at 63.3% on the
synthetic month. Do not weaken the check. See README "the bar and the default
contradict each other". Real partner statements settle it.

Next, roughly in order:
- Statement upload UI (file drop → parse preview with balance check → classify).
- Replace in-memory store with Postgres + SQLAlchemy (schema in trd.md §4);
  keep `review.state` as single source of truth, append-only AuditEvent.
- Auth (email OTP), roles operator/approver, maker-checker release step.
- Rules & memory screen; automation scoreboard (realized precision per tier).
- PDF parsing (pdfplumber, per-bank configs); real bank configs beyond the 4 stubs.
- Real Tier-3: AnthropicLLM batched (20 lines/call), few-shot from client memory.
- The Phase-0 rerun on REAL data: when real Tally exports + statements arrive,
  wire them into harness/ as golden sets — that result decides everything.
