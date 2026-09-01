"""The tiered classifier: T0 rules → T1 exact memory → T2 semantic memory →
T3 LLM (category) → ledger resolution against the client's chart.

Every suggestion carries tier, raw_score, evidence, and top-3 alternatives.
Calibration converts raw_score → confidence downstream (calibrate.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import taxonomy
from .memory import ClientMemory


@dataclass
class Suggestion:
    ledger_guid: str | None
    category_id: str | None
    tier: str                  # T0 | T1 | T2 | T3 | abstain
    raw_score: float
    alternatives: list = field(default_factory=list)   # [(guid, score)]
    evidence: list = field(default_factory=list)       # human-readable strings


class SemanticIndex:
    """Char n-gram TF-IDF kNN over the client's labeled narrations, plus the
    glossed-ledger index used for cold-start category→ledger mapping.
    Interface-compatible with a sentence-transformer swap (bge-small) later."""

    def __init__(self, memory: ClientMemory, ledgers: list[dict]):
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
        self.labels = [g for _, g in memory.labeled]
        texts = [t for t, _ in memory.labeled]
        self.ledgers = ledgers
        ledger_texts = [f"{l['name']} {l.get('gloss', '')}" for l in ledgers]
        corpus = texts + ledger_texts if texts else ledger_texts
        if not corpus:
            self.X = None
            return
        self.vec.fit(corpus)
        self.X = self.vec.transform(texts) if texts else None
        self.L = self.vec.transform(ledger_texts) if ledger_texts else None

    def nearest_labeled(self, normalized: str, k: int = 5) -> list[tuple[str, float]]:
        if self.X is None or not normalized:
            return []
        sims = cosine_similarity(self.vec.transform([normalized]), self.X)[0]
        order = np.argsort(-sims)[:k]
        return [(self.labels[i], float(sims[i])) for i in order if sims[i] > 0.05]

    def nearest_ledger(self, text: str, k: int = 3,
                       category_id: str | None = None) -> list[tuple[str, float]]:
        if self.L is None or not text:
            return []
        sims = cosine_similarity(self.vec.transform([text]), self.L)[0]
        pairs = list(zip(self.ledgers, sims))
        if category_id:  # prefer ledgers annotated with the category
            pairs = [(l, s + (0.35 if l.get("category_id") == category_id else 0.0))
                     for l, s in pairs]
        pairs.sort(key=lambda p: -p[1])
        return [(l["guid"], float(s)) for l, s in pairs[:k] if s > 0.05]


def annotate_ledgers(ledgers: list[dict]) -> list[dict]:
    """Bootstrap step: tag each client ledger with its best taxonomy category by
    gloss similarity (production: LLM annotates once, CA can edit)."""
    cats = taxonomy.load()
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    cat_texts = [f"{c['id'].replace('_', ' ')} {c['gloss']}" for c in cats]
    corpus = cat_texts + [f"{l['name']} {l.get('parent', '')}" for l in ledgers]
    vec.fit(corpus)
    C = vec.transform(cat_texts)
    for l in ledgers:
        sims = cosine_similarity(vec.transform([f"{l['name']} {l.get('parent', '')}"]), C)[0]
        l["category_id"] = cats[int(np.argmax(sims))]["id"]
        l.setdefault("gloss", "")
    return ledgers


def classify_line(line: dict, memory: ClientMemory, index: SemanticIndex,
                  llm, client_context: dict) -> Suggestion:
    """line: {narration, normalized, direction, amount, channel,
              counterparty_key, counterparty_name}"""
    # T0 — hard rules
    guid = memory.match_rule(line["normalized"])
    if guid:
        return Suggestion(guid, None, "T0", 1.0, evidence=["matched a client rule"])

    # T1 — exact counterparty memory (abstains on split history)
    guid, consistency, hist = memory.match_pattern(line["counterparty_key"])
    if guid:
        n = sum(hist.values())
        return Suggestion(guid, None, "T1", 0.90 + 0.09 * consistency,
                          alternatives=sorted(hist.items(), key=lambda kv: -kv[1])[:3],
                          evidence=[f"{n} prior transactions with this counterparty, "
                                    f"{consistency:.0%} to this ledger"])

    split_evidence = []
    if hist:
        split_evidence = [f"history is split: " +
                          ", ".join(f"{g[:8]}×{c}" for g, c in hist.items())]

    # T2 — semantic memory over labeled narrations
    nn = index.nearest_labeled(line["normalized"])
    if nn:
        votes: dict[str, float] = {}
        for g, s in nn:
            votes[g] = votes.get(g, 0.0) + s
        top = sorted(votes.items(), key=lambda kv: -kv[1])
        best_guid, _ = top[0]
        best_sim = max(s for g, s in nn if g == best_guid)
        agreement = votes[best_guid] / sum(votes.values())
        if best_sim >= 0.80 and agreement >= 0.6 and not hist:
            return Suggestion(best_guid, None, "T2", 0.55 + 0.4 * best_sim * agreement,
                              alternatives=top[:3],
                              evidence=[f"very similar past narration coded here "
                                        f"(sim {best_sim:.2f})"] + split_evidence)

    # T3 — LLM (category), then resolve to a ledger
    out = llm.classify(line, client_context)
    category = out["category_id"]
    guid = out.get("ledger_guid")
    score = out["raw_score"]
    evidence = [out.get("rationale", "")] + split_evidence
    alts: list = []
    if not guid:
        match_text = f"{line['counterparty_name']} {line['normalized']}"
        ranked = index.nearest_ledger(match_text, k=3, category_id=category)
        if ranked:
            guid, led_score = ranked[0]
            alts = ranked
            score = score * (0.55 + 0.45 * min(led_score, 1.0))
        else:
            return Suggestion(None, category, "abstain", 0.0,
                              evidence=evidence + ["no ledger candidate found"])
    return Suggestion(guid, category, "T3", min(score, 0.9),
                      alternatives=alts, evidence=evidence)
