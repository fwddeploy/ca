# Ledger Pilot PRD (v0.2, condensed)

**Vision:** upload a client's bank statement; get correctly classified Tally
vouchers in minutes. The firm reviews only what the AI is unsure about; every
correction makes next month better. Success: a 500-line statement = 10 minutes
of review, and the partner trusts what auto-posted.

**Users:** operator (junior accountant, daily, keyboard-first, must be faster
than their muscle memory); approver (partner/CA, signs, can veto after one bad
entry); client (indirect, answers questions via WhatsApp later).

**Core loop:** select client & upload → parse preview + balance check →
classification (every line: suggested ledger + voucher type + confidence +
evidence) → review queue (high-confidence pre-approved batch; low-confidence
sorted, grouped, bulk-apply, top-3 alternative chips with frequencies) → post
to Tally (validated, idempotent, undoable) → learn (corrections → client memory;
monthly accuracy card).

## Requirements (MoSCoW; M = v1 must)

**Ingestion:** M: digital PDF/Excel top-5 banks, format auto-detect; graceful
reject of scans/password files; balance-continuity validation; narration
enrichment (UTR/RRN/IFSC/GSTIN/PAN/VPA/cheque + channel). S: multi-account
batch, self-transfer netting. L: scans/photos via vision, invoices, client
upload link, Account Aggregator (keep ingestion source-agnostic).

**Classification:** M: Tier-0 hard rules (one-click from correction, stable,
never silently rewritten); cold start via category taxonomy → glossed-ledger
matching (≥70% day-zero top-1); calibrated 3-tier confidence with provenance
badges RULE/MEMORY/AI, conformal per-client threshold (≤2% error on
auto-approved); client memory keyed to ledger GUIDs; bootstrap from Tally
history. S: new-ledger proposals; cross-client anonymized priors; GSTR-2B
matching (bank↔2B, via GSP, OTP consent); outlier check independent of
confidence + materiality escalation. L: GST/TDS treatment per line.

**Review:** M: confidence-sorted keyboard-first queue (500 lines ≤10 min);
top-3 chips with historical frequencies; evidence-on-demand; narration grouping
with bulk apply; "ready to post" batch-accept; nothing exports unreviewed;
explicit reviewed-state per line (auto/suggested/human + who/when); zero-touch
auto-post is per-client opt-in. S: maker-checker; ask-the-client WhatsApp loop
(batched, replies feed memory).

**Tally:** M: valid TallyPrime voucher XML, pre-validated against synced
masters, closest-ledger suggestions on mismatch. S: desktop connector
(idempotent via AlterID, one-click batch undo, offline XML always available).
L: Tally-on-cloud, Zoho/Busy adapters.

**Workspace:** M: client status board + automation scoreboard (auto-rate trend,
realized precision per tier, hours saved). S: roles/seats. L: doc chasing.

**Non-functional:** privacy (classification-only processing, no training on
identifiable data, local/hybrid path credible), 500 lines < 2 min,
explainability on every line, survives browser close.

**Metrics:** precision above threshold ≥98% (the ruling metric); auto-rate 70%
day zero → 90% by month 6; review ≤10 min/500 lines; ≥80% time saved; design
partner posts weekly with no gaps.

**v1 scope rule:** if it doesn't make classification more accurate or review
faster, it waits. Not in v1: invoices, scans, connector, GST recon,
client-facing uploads, multi-firm admin, billing, non-Tally.

**Open questions:** taxonomy granularity; auto-post vs pre-approve-visible
(suggest visible-first for 2 months per firm); cloud vs hybrid processing;
bill-reference matching in v1 (suggest holding ledger); name TBD.
