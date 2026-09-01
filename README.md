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

| Scenario | Suggestion top-1 | Auto-classified | Auto precision |
|---|---|---|---|
| Cold start (masters only) | 73.5% | 0% (conservative by design) | — |
| 6 months history + calibrated threshold | 98.0% | 87.8% | 100% |
| Month 2 after corrections (cold client) | 97.9% | 66.7% | 100% |

Zero dangerous auto-posts in all runs; balance checks pass; materiality and
first-seen-counterparty guardrails route the risky lines to review.

**These are synthetic numbers.** The real Phase 0 gate runs this same harness on
design-partner data: their Tally voucher exports + matching bank statements.
Drop them in, point `harness/run.py` at them, and the go/no-go is measured the
same way.

## Next (M1, per TRD)

FastAPI server + data model → upload flow → review queue UI (grouping, top-3
chips, keyboard-first) → calibrated routing → XML export → design-partner pilot.
