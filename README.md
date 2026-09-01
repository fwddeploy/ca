# Ledger Pilot — M0

AI ledger classification for Indian CA firms: bank statement in → classified,
confidence-scored suggestions → human reviews exceptions → Tally vouchers out →
learns from every correction.

This is **M0** from the TRD: the classification engine and the accuracy harness,
built before any UI so every claim is measured, not hoped.

## Layout

```
engine/    parsers (bank YAML configs, balance-continuity check), enrichment
           (UTR/RRN/VPA/GSTIN extraction, channel detection, counterparty keys),
           taxonomy.yaml (~45 Indian CoA categories, glossed), memory (T0 rules +
           T1 patterns, ledger-GUID-keyed, rule-stable), tiers (T0→T1→T2→T3),
           llm (Mock offline + Anthropic production adapter), calibrate
           (conformal-style threshold, materiality guardrails), pipeline
bridge/    Tally voucher/masters/undo XML with idempotent LP-{batch}-{seq} refs
harness/   synthetic golden-set generator + replay runner (go/no-go gate)
tests/     8-test suite
```

## Run

```
python3 tests/test_all.py     # unit + end-to-end
python3 -m harness.run        # the accuracy report and go/no-go checks
```

No external services needed — Tier 3 uses the deterministic MockLLM offline.
Production: `pip install anthropic`, set `ANTHROPIC_API_KEY` and `LP_USE_REAL_LLM=1`.

## Current synthetic-harness numbers (Sep 2026)

Reproduced on a clean machine, 49-line test month. **An auto-rate is meaningless
without the materiality it ran at** — the guardrail queues every line at or above
that amount regardless of confidence, so a generous materiality flatters the
auto-rate while changing nothing about the engine.

At **₹2,00,000** materiality — `python -m harness.run`:

| Scenario | Suggestion top-1 | Auto-classified | Auto precision |
|---|---|---|---|
| Cold start (masters only) | 73.5% | 0% (conservative by design) | — |
| 6 months history + calibrated threshold | 98.0% | 87.8% | 100% |
| Month 2 after corrections (cold client) | 97.9% | 66.7% | 100% |

At the **₹50,000 the product actually ships** (`calibrate.MATERIALITY_DEFAULT`) —
`python -m harness.run --materiality 50000`:

| Scenario | Suggestion top-1 | Auto-classified | Auto precision |
|---|---|---|---|
| Cold start (masters only) | 73.5% | 0% | — |
| 6 months history + calibrated threshold | 98.0% | **59.2%** | 100% |
| Month 2 after corrections (cold client) | 97.9% | **45.8%** | 100% |

Zero dangerous auto-posts in every run; balance checks pass; precision holds at
100% throughout. Suggestion accuracy is unchanged — only the routing moves.

### Open: the bar and the default contradict each other

On this test month, 18 of 49 lines (36.7%) sit at or above ₹50,000, so the
materiality guardrail alone caps the auto-rate at **63.3%**. The TRD §10 check
"warm auto ≥ 75%" is therefore *arithmetically unreachable* at the shipped
default — no engine improvement can pass it. One of three things is wrong: the
₹50,000 default, the 75% bar, or the harness's ₹2,00,000. **Do not resolve this
by weakening the check.** It is a question for real partner statements: the
synthetic client is a Surat textile trader with a median line of ₹31,175 and a
p75 of ₹63,058, and a different client's amount distribution moves this number
a long way.

**These are synthetic numbers.** The real Phase 0 gate runs this same harness on
design-partner data: their Tally voucher exports + matching bank statements.
Drop them in, point `harness/run.py` at them, and the go/no-go is measured the
same way.

## The app

```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn api.main:app --port 8000
```

Upload is deliberately two steps, because a statement that does not reconcile
was either mis-parsed or edited, and neither belongs in front of a CA:

| Step | Endpoint | What it does |
|---|---|---|
| 1 | `POST /api/clients/{cid}/upload` | Parses only. Returns the detected bank, row count, period, opening/closing, the balance-continuity verdict and the rows that break it, plus a sample. **Classifies nothing.** |
| 2 | `POST /api/clients/{cid}/previews/{pid}/classify` | Runs the engine. **409 if the balance chain broke** (TRD §5), naming the rows. |

Posting is idempotent: `POST /statements/{sid}/post` returns the same batch id
every time, `GET /statements/{sid}/xml` is a read that 409s until posted, and
`POST /statements/{sid}/undo` emits the cancellation XML for a posted batch.
One statement is only ever one `LP-{batch}` in Tally.

Rejected gracefully: PDFs and scans (v1.5), non-tabular or password-protected
files, and column layouts no bank config matches — the error names the columns
it actually saw.

## Next (M1 → M2, per TRD)

Postgres + SQLAlchemy behind the same shapes · email-OTP auth and the
maker-checker release step · rules & memory screen · automation scoreboard ·
pdfplumber ingestion and real bank configs beyond the four stubs · batched
AnthropicLLM Tier 3 · and the one that decides everything, the Phase-0 rerun
of `harness/run.py` on a design partner's real Tally exports.
