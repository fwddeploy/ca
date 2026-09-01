"""Confidence calibration + auto-approve thresholding.

Approach (per TRD §6): per-tier score bands are already ordinal; the guarantee
comes from the threshold rule — pick the lowest threshold t such that the
observed error rate among calibration lines with score ≥ t is ≤ target with a
finite-sample correction ((errors+1)/(n+1), Learn-then-Test flavour). Until a
client has enough reviewed lines, a conservative global default applies and a
sample of auto-approved lines is routed to review anyway.
"""
from __future__ import annotations

from dataclasses import dataclass

CONSERVATIVE_DEFAULT = 0.92
MIN_CALIBRATION_LINES = 200
TARGET_ERROR = 0.02
AUDIT_SAMPLE_RATE = 0.10   # while uncalibrated, 10% of auto-approved lines get review
MATERIALITY_DEFAULT = 50_000.0


@dataclass
class Threshold:
    value: float
    calibrated: bool
    n_calibration: int
    expected_error: float


def fit_threshold(calibration: list[tuple[float, bool]],
                  target_error: float = TARGET_ERROR) -> Threshold:
    """calibration: [(raw_score, was_correct)] from reviewed lines."""
    n = len(calibration)
    if n < MIN_CALIBRATION_LINES:
        return Threshold(CONSERVATIVE_DEFAULT, False, n, target_error)
    pts = sorted(calibration, key=lambda p: -p[0])
    best = None
    errors = 0
    for i, (score, correct) in enumerate(pts, start=1):
        if not correct:
            errors += 1
        # a threshold only exists at a score boundary: t = score includes every
        # point with the same score, so evaluate where the next score is lower
        if i < len(pts) and pts[i][0] == score:
            continue
        adjusted = (errors + 1) / (i + 1)          # finite-sample upper-ish bound
        if adjusted <= target_error:
            best = (score, adjusted)
    if best is None:
        return Threshold(1.01, True, n, target_error)  # nothing auto-approves
    return Threshold(best[0], True, n, best[1])


def route(suggestion, amount: float, threshold: Threshold,
          materiality: float = MATERIALITY_DEFAULT,
          first_seen_counterparty: bool = False) -> str:
    """→ 'auto_approved' | 'queue'. Materiality guardrail: big or first-ever
    counterparty lines queue regardless of confidence (PRD CLS-8)."""
    if suggestion.ledger_guid is None:
        return "queue"
    if amount >= materiality and suggestion.tier != "T0":
        return "queue"
    if first_seen_counterparty and amount >= materiality / 2:
        return "queue"
    if suggestion.raw_score >= threshold.value:
        return "auto_approved"
    return "queue"


def realized_precision(reviewed: list[tuple[float, bool]], threshold: float) -> tuple[float, int]:
    """The scoreboard number: of lines at/above threshold, how many were right."""
    above = [(s, c) for s, c in reviewed if s >= threshold]
    if not above:
        return 1.0, 0
    return sum(1 for _, c in above if c) / len(above), len(above)
