"""Taxonomy loading + category→ledger matching (the cold-start mapping trick:
match against *glossed* ledger names, not bare idiosyncratic names)."""
from __future__ import annotations

from pathlib import Path

import yaml

_TAX = None


def load() -> list[dict]:
    global _TAX
    if _TAX is None:
        _TAX = yaml.safe_load((Path(__file__).parent / "taxonomy.yaml").read_text())["categories"]
    return _TAX


def by_id(cid: str) -> dict | None:
    return next((c for c in load() if c["id"] == cid), None)


def keyword_category(text: str, direction: str) -> tuple[str | None, float]:
    """Weak-supervision labeling function: keyword hit → category.
    Used by the mock LLM and as a T3 prior. Returns (category_id, strength)."""
    t = " " + text.lower() + " "
    best, best_len = None, 0
    for cat in load():
        for kw in cat["keywords"]:
            if kw.lower() in t and len(kw) > best_len:
                side = cat.get("side", "any")
                if side in ("any", "contra") or side == direction:
                    best, best_len = cat["id"], len(kw)
    return best, (0.75 if best else 0.0)
