# Decisions & research lessons (do not relitigate casually)

From a 6-track global research sweep (US/UK/platforms/Japan/India/academia).
Full sources live in the project's research artifacts; the conclusions:

**Validated by published numbers:** Intuit production research: 68% zero-shot
on a never-seen company (our 70% cold bar is realistic); 88% top-5 with
history; accuracy collapses 79%→48% without per-company context — the
per-client memory IS the product. Calibrated confidence >0.8 → 90%+ accuracy
(sea.dev). FreeAgent's honest ceiling: ~80% coverage — some lines need info not
in the statement (hence the ask-the-client loop).

**Architecture decisions:**
- Deterministic-before-ML cascade (FreeAgent, Xero, freee all converged here).
  Same narration → same ledger, always. JAX's flip-flopping was its most
  damning field failure.
- Corrections → client-scoped rules/patterns instantly; global model retrains
  later. Never let one odd transaction rewrite a learned rule (Money Forward's
  most-hated bug; freee got it right).
- Confidence = 3 visible tiers + provenance badges (RULE/MEMORY/AI), never a
  raw number in UI. Low-confidence *match* suggestions: don't show at all.
- Batch-accept "ready to post"; true zero-touch = per-client earned opt-in.
- Group review (cluster similar lines, one decision per group) is the biggest
  review-speed lever. Top-3 chips: top-5 accuracy runs ~20pts above top-1.
- Independent outlier check (Sage Intacct pattern): right-ledger-absurd-amount
  is the expensive miss confidence can't catch.
- Key memory to ledger GUIDs (GnuCash rename bug).
- Fence bulk actions away from AR/AP-linked lines; check open bills before
  creating vouchers (JAX×Dext timing collision).

**Business lessons (the graveyard):** Botkeeper died as services-in-software-
costume ($100M); Bench's proprietary ledger = hostage crisis ("your books stay
in YOUR Tally" is our sales weapon); Dext's pricing rug-pull caused an exodus
(publish prices, grandfather early partners, never charge premium for bank
pages — they're our core object); Hubdoc = what post-acquisition neglect looks
like (silent auto-publish + duplicates kill trust fastest).

**India specifics:** VPA is the stable counterparty key; balance-continuity
check catches both parse errors and tampered PDFs; GSTR-2B↔bank matching (via
GSP as ASP, OTP consent, no license) is an open seam nobody exploits; Account
Aggregator can't onboard non-regulated entities yet — keep ingestion
source-agnostic and wait. TallyPrime 6+ imports statements natively but still
makes users type every ledger name — classification is our whole gap.
Competitive timing: format detection/bulk approve are commodity; rule-stable
per-client learning + WhatsApp per-transaction loop + maker-checker at scale
are the 12–24-month moats. Watch Vyapar TaxOne (fast follower) and freee's
Sept-2026 agent suite (Japan leads India by ~12–18 months).

**Product stance (founder):** the differentiator is accuracy WITHOUT client
data — with data everyone is king. ~70% out of the box, learn the rest.
Precision above threshold is the ruling metric; never trade it for auto-rate.
