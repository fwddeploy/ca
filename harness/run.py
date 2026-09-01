"""Accuracy harness: replays statements against known-correct ledgers and reports
the go/no-go metrics from the PRD/TRD. Same code path the scoreboard uses later.

Usage: python -m harness.run [--materiality 200000]

Materiality is an *input*, not a constant, and every report states the value it
ran at. An auto-rate quoted without its materiality is not a number: the
guardrail queues every line at or above it regardless of confidence, so a
generous materiality flatters the auto-rate while changing nothing about the
engine. HARNESS_MATERIALITY is what the go/no-go bars in TRD 10 were set
against; calibrate.MATERIALITY_DEFAULT is what the product actually ships.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# A default Windows console is cp1252, and this report prints ₹, ≥, → and box
# rules. Without this line `python -m harness.run` dies on its very first print
# with UnicodeEncodeError — found by running a clean clone in a plain shell.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import calibrate                                # noqa: E402
from engine.memory import ClientMemory                      # noqa: E402
from engine.parsers import ParsedLine, ParsedStatement, validate_balance  # noqa: E402
from engine.pipeline import classify_statement              # noqa: E402
from harness.synth import gen_month, history_vouchers, make_client  # noqa: E402

HARNESS_MATERIALITY = 200_000.0   # the value the TRD 10 bars were calibrated at


def rows_to_stmt(rows) -> ParsedStatement:
    lines = [ParsedLine(r["date"], r["narration"], r["amount"], r["direction"], r["balance"])
             for r in rows]
    o, c, ok, br = validate_balance(lines)
    return ParsedStatement("SYNTH", lines, o, c, ok, br)


def score(result, truth, label: str, suspense_guid: str) -> dict:
    n = len(result.lines)
    auto = [(l, t) for l, t in zip(result.lines, truth) if l.state == "auto_approved"]
    queued = n - len(auto)
    auto_correct = sum(1 for l, t in auto if l.suggestion.ledger_guid == t)
    # suggestion quality on queued lines (does the right answer surface anyway?)
    q = [(l, t) for l, t in zip(result.lines, truth) if l.state == "queue"]
    top1_q = sum(1 for l, t in q if l.suggestion.ledger_guid == t)
    top3_q = sum(1 for l, t in q
                 if t in ([l.suggestion.ledger_guid] +
                          [g for g, _ in l.suggestion.alternatives]))
    # oddballs: truth == suspense/cash lines must NOT auto-post to a wrong ledger
    dangerous = sum(1 for l, t in auto if t == suspense_guid)
    overall_top1 = sum(1 for l, t in zip(result.lines, truth)
                       if l.suggestion.ledger_guid == t) / n
    overall_top3 = sum(1 for l, t in zip(result.lines, truth)
                       if t in ([l.suggestion.ledger_guid] +
                                [g for g, _ in l.suggestion.alternatives])) / n
    m = {
        "label": label, "lines": n,
        "auto_rate": len(auto) / n,
        "auto_precision": (auto_correct / len(auto)) if auto else 1.0,
        "queued": queued,
        "top1": overall_top1, "top3": overall_top3,
        "queue_top1": top1_q / max(len(q), 1),
        "queue_top3": top3_q / max(len(q), 1),
        "dangerous_autoposts": dangerous,
        "balance_ok": result.statement.balance_ok,
    }
    print(f"\n── {label} ──")
    print(f"  lines {n} · balance check {'PASS' if m['balance_ok'] else 'FAIL'}")
    print(f"  suggestion accuracy  top-1 {overall_top1:5.1%} · top-3 {overall_top3:5.1%}")
    print(f"  auto-classified   {m['auto_rate']:6.1%}   precision {m['auto_precision']:6.1%}")
    print(f"  queued            {queued:4d}     top-1 {m['queue_top1']:5.1%} · top-3 {m['queue_top3']:5.1%}")
    print(f"  dangerous auto-posts (should be 0): {dangerous}")
    return m


def main(materiality: float = HARNESS_MATERIALITY):
    client = make_client()
    suspense = client.guid_of("Suspense")
    test_rows, truth = gen_month(client, "2026-08", seed=999)
    stmt = rows_to_stmt(test_rows)

    shipped = calibrate.MATERIALITY_DEFAULT
    print(f"materiality guardrail ₹{materiality:,.0f}"
          + ("" if materiality == shipped
             else f"   (product default is ₹{shipped:,.0f} — these numbers are NOT at the shipped default)"))
    print(f"target error {calibrate.TARGET_ERROR:.0%} · conservative threshold "
          f"{calibrate.CONSERVATIVE_DEFAULT} until {calibrate.MIN_CALIBRATION_LINES} reviewed lines")

    # ── COLD START: no history, only the ledger masters ──
    cold_mem = ClientMemory(client.client_id)
    cold = classify_statement(stmt, cold_mem, client.ledgers, business=client.business,
                              materiality=materiality)
    m_cold = score(cold, truth, "COLD START (day zero — masters only)", suspense)

    # ── WARM: 6 months of Tally history bootstrapped ──
    warm_mem = ClientMemory(client.client_id)
    hist = history_vouchers(client, [f"2026-{mm:02d}" for mm in range(2, 8)])
    warm_mem.bootstrap_from_history(hist)
    # calibration set: replay held-out months and mark correctness (~200+ lines)
    calibration = []
    for k, mth in enumerate(["2026-05", "2026-06", "2026-07", "2026-04", "2026-03"]):
        cal_rows, cal_truth = gen_month(client, mth, seed=555 + k)
        cal_res = classify_statement(rows_to_stmt(cal_rows), warm_mem, client.ledgers,
                                     business=client.business, materiality=materiality)
        calibration += [(l.suggestion.raw_score, l.suggestion.ledger_guid == t)
                        for l, t in zip(cal_res.lines, cal_truth) if l.suggestion.ledger_guid]
    warm = classify_statement(stmt, warm_mem, client.ledgers, business=client.business,
                              calibration=calibration, materiality=materiality)
    m_warm = score(warm, truth, f"WITH HISTORY (6 months · {len(hist)} vouchers · "
                   f"threshold {warm.threshold.value:.2f} "
                   f"{'calibrated' if warm.threshold.calibrated else 'conservative default'})",
                   suspense)

    # ── LEARNING LOOP: corrections from month 1 feed month 2 ──
    for l, t in zip(cold.lines, truth):
        cold_mem.learn(l.counterparty_key, l.normalized, t)
    rows2, truth2 = gen_month(client, "2026-09", seed=1234)
    month2 = classify_statement(rows_to_stmt(rows2), cold_mem, client.ledgers,
                                business=client.business, materiality=materiality)
    m_l = score(month2, truth2, "MONTH 2 AFTER CORRECTIONS (cold client, learned)", suspense)

    print(f"\n── GO / NO-GO (TRD §10) — at materiality ₹{materiality:,.0f} ──")
    checks = [
        ("cold suggestion top-1 ≥ 70% (the PRD day-zero bar)", m_cold["top1"] >= 0.70),
        ("cold auto precision ≥ 95%", m_cold["auto_precision"] >= 0.95),
        ("warm auto ≥ 75% with calibrated threshold", m_warm["auto_rate"] >= 0.75),
        ("warm auto precision ≥ 98%", m_warm["auto_precision"] >= 0.98),
        ("month-2 learning beats cold start", m_l["auto_rate"] > m_cold["auto_rate"]),
        ("zero dangerous auto-posts", m_cold["dangerous_autoposts"] +
         m_warm["dangerous_autoposts"] + m_l["dangerous_autoposts"] == 0),
    ]
    ok = True
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok &= passed
    print(f"\n{'ALL CHECKS PASS' if ok else 'CHECKS FAILED'} "
          f"at materiality ₹{materiality:,.0f} "
          "(synthetic data — real go/no-go runs on partner statements)")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--materiality", type=float, default=HARNESS_MATERIALITY,
                    help=f"queue-regardless-of-confidence threshold in rupees "
                         f"(harness default {HARNESS_MATERIALITY:,.0f}; "
                         f"product ships {calibrate.MATERIALITY_DEFAULT:,.0f})")
    raise SystemExit(main(ap.parse_args().materiality))
