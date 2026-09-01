# State — 2026-09-01

Written before the context wall, not after. If a session ends here, this is
enough to carry on.

## Where the project lives

`C:\Users\DELL\Projects\ledger-pilot`, git repo, remote
`https://github.com/fwddeploy/ca.git` (**public**, currently **empty — nothing
pushed yet**; the owner has not been asked for the push). Origin of the code:
`C:\Users\DELL\Downloads\ledger-pilot-handoff_1.zip`.

Environment: Python 3.12.10, project `.venv`, `requirements.txt` pinned loose.
Run `.venv\Scripts\python -m pytest tests\test_all.py -q` and
`.venv\Scripts\python -m harness.run`.

## What was done, in order

| # | Module | Commit | Evidence |
|---|---|---|---|
| A | Repo setup, deps, baseline verified | `8e8b01c` | 8/8 tests, harness ALL CHECKS PASS — the handoff's README numbers reproduce exactly |
| B | Materiality made explicit and reported | `33f0b44` | E3 — see the contradiction below |
| C | Posting made idempotent, reads made pure, `/undo` exposed | `5a45066` | 3 tests mutation-checked E3, 1 at E2 |
| D | Two-step upload with the balance gate | `eefa1be` | 4 tests mutation-checked E3, plus live-server verification |
| E | Postgres + SQLAlchemy store | `03ce570` + this | 20/20 on **both** SQLite and Postgres |

Tests 8 → 20, all green on both backends. Harness green at ₹2,00,000.

## The open question the owner must settle

`harness/run.py --materiality 50000` **FAILS** the TRD §10 check
"warm auto ≥ 75%", and cannot be made to pass:

- 18 of 49 lines in the synthetic test month are ≥ ₹50,000
- so the materiality guardrail alone caps the auto-rate at **63.3%**
- observed 59.2%; precision stays 100%, dangerous auto-posts stay 0

The ₹50,000 default (`calibrate.MATERIALITY_DEFAULT`), the 75% bar (TRD §10)
and the harness's ₹2,00,000 are mutually inconsistent. **The check was left
failing rather than weakened** (CLAUDE.md principle 6). Only real partner
statements can settle which of the three is wrong — the synthetic client is a
Surat textile trader, median line ₹31,175, p75 ₹63,058.

`api/main.py` also runs at ₹2,00,000, now with a comment saying why and the
value surfaced in `/queue` so the UI states it.

## Evidence levels

Everything here is **E3 at best**. The data is synthetic, and synthetic data
answers "does it survive", never "is it accurate". E4/E5 needs a design
partner's real Tally exports plus matching bank statements dropped into
`harness/`. Nothing about readiness should be claimed until that runs.

## Databases on this machine

- **5432** `tenderwatch-postgres-1` — **another project. Do not touch it.**
- **5433** `ledger-pilot-postgres` (`docker compose up -d`), databases
  `ledger_pilot` and `ledger_pilot_test`. The test one is wiped on every run.

## Not started

Auth (email OTP) and roles · maker-checker release · rules & memory screen ·
automation scoreboard · pdfplumber ingestion and real bank configs beyond the
four stubs · batched AnthropicLLM Tier 3 · **the Phase-0 rerun on real partner
data, which is the only thing that moves any claim above E3.**
