# Ledger Pilot TRD (v0.1, condensed)

**Stack:** React+TS+Vite+TanStack Query (SPA today is a single-file placeholder —
migrate when the queue UI grows); FastAPI + SQLAlchemy + Pydantic monolith,
Python 3.12; Postgres 16 + pgvector (one DB for relational + vectors);
pdfplumber/openpyxl/pandas; embeddings bge-small (sentence-transformers,
self-hosted; char-ngram TF-IDF is the current stand-in in tiers.py — same
interface); LLM = Claude Haiku-class Tier 3 via provider-agnostic adapter,
batched 20 lines/call; Postgres job table + worker (no Redis/Celery in v1);
email-OTP auth, roles operator/approver/admin; v2 Tally agent = Go/Rust single
Windows binary. Infra: one India region (data residency), encrypted object
storage for statements.

## Data model (§4)

Firm → User(role) · Client → BankAccount → Statement → TransactionLine;
Client → LedgerMaster(guid, name, parent, gstin, synced_at) · Rule(pattern,
match_type exact/prefix/suffix/partial, ledger_guid, scope, stable) ·
MemoryPattern(counterparty_key → {guid: count}) · CalibrationSet ·
VoucherBatch → Voucher(draft→approved→posted→undone).

TransactionLine: date, narration_raw/norm, amount, direction, refs{utr,rrn,vpa,
ifsc,gstin,cheque}, channel, counterparty_key, suggestion{ledger_guid,
voucher_type, tier, raw_score, confidence, alternatives[3], evidence[]},
review{state auto_approved|suggested|human_reviewed|asked_client|suspense,
reviewed_by, reviewed_at, corrected_to}. AuditEvent append-only.

Rules: ledger refs are Tally GUIDs (names denormalized for display);
review.state is the single source of truth; corrections preserve what the AI
said (calibration trains on the delta).

## Engine (§5–6)

Ingestion: per-bank YAML configs (monopoly-style), balance-continuity check
blocks classification and names breaking rows; unknown format → LLM-assisted
extraction accepted only if balance math passes, auto-drafts a new config.
Enrichment: strict regex (UTR 16-char, RRN 12-digit, IFSC, GSTIN check-digit,
PAN, VPA, cheque) → channel → counterparty_key (VPA wins) → normalized text
(refs/rails stripped) for embedding.

Classifier ladder: T0 hard rules (conf 1.0, Money-Forward match types,
specificity exact>prefix/suffix>partial, client scope beats firm) → T1 exact
memory (support ≥3, consistency ≥0.8, else abstain-with-evidence) → T2
embedding kNN over client's labeled lines + taxonomy-glossed ledger names
(cold-start mapping) → T3 LLM few-shot (client context + glossed ledger list +
similar examples; category → ledger resolution). Calibration: per-client
threshold via finite-sample rule ((errors+1)/(n+1) ≤ 2%) at score boundaries;
< 200 reviewed lines → conservative 0.92 default + 10% audit sampling.
Guardrails: materiality (default ₹50k, firm-configurable) and first-seen
counterparty queue regardless of confidence; T0 exempt.

## API (§7)

GET /statements/:id/queue (groups + singles + readyToPost); POST
/lines/:id/review {accept|reassign|suspense}; POST group review (atomic to
twins); POST /statements/:id/approve (maker-checker); every review returns any
rule proposal; all writes append AuditEvents; audit exportable per statement.

## Tally bridge (§8)

Masters in via Tally XML export upload (v2: live over port 9000). Vouchers out
as ENVELOPE/TALLYMESSAGE (receipt/payment/contra), narration carries bank
narration + `LP-{batch}-{seq}` (idempotency key). Pre-export validation: every
GUID exists in synced masters; locked/GST-filed periods block. Undo = batch
cancellation XML. New ledgers → masters XML imported first. Golden-file test
per supported Tally version; an XML that fails to import at the firm is P0.

## Security (§9)

India region; LLM gets narration + ledger names only, zero-retention config;
firm_id scoping on every query; append-only audit; masked narrations in logs.

## Harness (§10) — the gate

Replays statements vs known-correct postings. Reports: auto-rate at threshold,
precision above threshold, top-1/3, per-tier contribution, calibration curves,
cost. Bar: ≥70% cold top-1, ≥80% auto with history, ≥98% precision everywhere,
zero dangerous auto-posts. Golden sets frozen; every engine change runs it.
Same codepath powers the customer scoreboard.

## Milestones

M0 engine+harness (DONE, synthetic-green) · M1 API/DB/UI end-to-end at partner
firm (demo app exists; needs Postgres, auth, upload UI) · M2 connector, scans,
maker-checker, scoreboard, 10 firms · M3 GSTR-2B, WhatsApp loop, agent.

Open: LLM region/retention for CA trust; RLS now or at firm #2; partner's Tally
version/setup; taxonomy bottom-up from partner ledgers (recommended) vs ICAI-down.
