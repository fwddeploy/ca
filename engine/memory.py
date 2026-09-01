"""Client memory: hard rules (T0) and learned counterparty patterns (T1).

Keys are Tally ledger GUIDs, never names (renames must not orphan memory).
Rule stability: corrections update pattern support; they only *propose* rules.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

MATCH_TYPES = ("exact", "prefix", "suffix", "partial")  # Money-Forward spec


@dataclass
class Rule:
    pattern: str
    match_type: str          # exact | prefix | suffix | partial
    ledger_guid: str
    scope: str = "client"    # client | firm
    created_from: str = "manual"

    def matches(self, normalized: str) -> bool:
        p, n = self.pattern.lower(), normalized.lower()
        return {"exact": n == p, "prefix": n.startswith(p),
                "suffix": n.endswith(p), "partial": p in n}[self.match_type]


@dataclass
class ClientMemory:
    client_id: str
    rules: list = field(default_factory=list)                 # [Rule]
    patterns: dict = field(default_factory=dict)              # key -> {guid: count}
    labeled: list = field(default_factory=list)               # [(normalized, guid)] for T2/T3

    # ---- T0 ----
    def match_rule(self, normalized: str) -> str | None:
        # specificity: exact > prefix/suffix > partial; client scope beats firm
        ranked = sorted(self.rules, key=lambda r: (
            0 if r.scope == "client" else 1, MATCH_TYPES.index(r.match_type)))
        for r in ranked:
            if r.matches(normalized):
                return r.ledger_guid
        return None

    # ---- T1 ----
    def match_pattern(self, counterparty_key: str, min_support: int = 3,
                      min_consistency: float = 0.8) -> tuple[str | None, float, dict]:
        """Return (guid, consistency, history). Abstains when history is split."""
        hist = self.patterns.get(counterparty_key)
        if not hist:
            return None, 0.0, {}
        total = sum(hist.values())
        guid, top = max(hist.items(), key=lambda kv: kv[1])
        consistency = top / total
        if total >= min_support and consistency >= min_consistency:
            return guid, consistency, hist
        return None, consistency, hist   # known-but-ambiguous: evidence for T3, no auto

    # ---- learning ----
    def learn(self, counterparty_key: str, normalized: str, ledger_guid: str) -> dict | None:
        """Record a confirmed classification. Returns a rule *proposal* when the
        pattern just crossed the support bar (never auto-creates the rule)."""
        if counterparty_key:
            hist = self.patterns.setdefault(counterparty_key, {})
            hist[ledger_guid] = hist.get(ledger_guid, 0) + 1
        if normalized:
            self.labeled.append((normalized, ledger_guid))
        if counterparty_key:
            guid, cons, hist = self.match_pattern(counterparty_key)
            if guid and sum(hist.values()) == 3:   # exactly crossed the bar
                return {"pattern": counterparty_key, "ledger_guid": guid,
                        "proposal": "always classify this counterparty here?"}
        return None

    def bootstrap_from_history(self, vouchers: list) -> int:
        """vouchers: [{narration_norm, counterparty_key, ledger_guid}] from Tally export."""
        for v in vouchers:
            if v.get("counterparty_key"):
                hist = self.patterns.setdefault(v["counterparty_key"], {})
                hist[v["ledger_guid"]] = hist.get(v["ledger_guid"], 0) + 1
            if v.get("narration_norm"):
                self.labeled.append((v["narration_norm"], v["ledger_guid"]))
        return len(vouchers)

    # ---- persistence ----
    def save(self, path: str | Path):
        Path(path).write_text(json.dumps({
            "client_id": self.client_id,
            "rules": [asdict(r) for r in self.rules],
            "patterns": self.patterns, "labeled": self.labeled}, indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "ClientMemory":
        d = json.loads(Path(path).read_text())
        m = cls(d["client_id"], patterns=d["patterns"], labeled=[tuple(x) for x in d["labeled"]])
        m.rules = [Rule(**r) for r in d["rules"]]
        return m
