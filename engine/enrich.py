"""Narration enrichment: reference extraction, channel detection, counterparty key.

The counterparty_key is the substrate the client memory is built on — a stable,
normalized identifier for "who is on the other side" of a transaction line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RE_VPA = re.compile(r"\b([a-z0-9._-]{2,64})@([a-z]{2,20})\b", re.I)
RE_UTR_NEFT = re.compile(r"\b([A-Z]{4}[RN]\d{11})\b")
RE_RRN = re.compile(r"\b(\d{12})\b")
RE_IFSC = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
RE_GSTIN = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z]\d)\b")
RE_PAN = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")
RE_CHQ = re.compile(r"\bCHQ\.?\s*(?:NO\.?\s*)?(\d{6})\b", re.I)
RE_MASKED = re.compile(r"[xX*]{3,}\d{2,4}")
RE_NUMS = re.compile(r"\b\d{5,}\b")

CHANNEL_PATTERNS = [
    ("reversal", re.compile(r"\b(REV|RVSL|REVERSAL|RET(URN)?ED?|NEFT RETURN)\b", re.I)),
    ("bank_charges", re.compile(r"\b(CHRG|CHARGES?|CHGS|SMS ALERT|AMC|CONSOLIDATED CHARGES|MIN BAL|PENAL)\b", re.I)),
    ("bank_interest", re.compile(r"\b(INT\.?\s?(CR|PD|CREDIT|COLL)|INTEREST)\b", re.I)),
    ("cash", re.compile(r"\b(CASH DEP|CSH DEP|ATM|CASH WDL|SELF|BY CASH|CDM)\b", re.I)),
    ("cheque", re.compile(r"\b(CHQ|CHEQUE|MICR|CTS|CLG)\b", re.I)),
    ("salary", re.compile(r"\b(SALARY|SAL |PAYROLL|STIPEND)\b", re.I)),
    ("ach", re.compile(r"\b(ACH|NACH|ECS|MANDATE|AUTOPAY|SI-)\b", re.I)),
    ("upi", re.compile(r"\bUPI\b", re.I)),
    ("card", re.compile(r"\b(POS|ECOM|VISA|MASTERCARD|RUPAY|CARD)\b", re.I)),
    ("imps", re.compile(r"\bIMPS\b", re.I)),
    ("rtgs", re.compile(r"\bRTGS\b", re.I)),
    ("neft", re.compile(r"\bNEFT\b", re.I)),
    ("transfer", re.compile(r"\b(TRF|TRANSFER|FT-)\b", re.I)),
]

NOISE_TOKENS = re.compile(
    r"\b(UPI|NEFT|IMPS|RTGS|ACH|NACH|ECS|DR|CR|TRF|FT|P2A|P2M|MB|IB|INB|REF|"
    r"PAYMENT|PAYMT|COLLECT|OKICICI|OKHDFCBANK|OKSBI|OKAXIS|YBL|PYTM|IBL|AXL|PAYTM)\b", re.I)


@dataclass
class Enriched:
    narration: str
    refs: dict = field(default_factory=dict)
    channel: str = "other"
    counterparty_key: str = ""
    counterparty_name: str = ""
    normalized: str = ""


def detect_channel(narration: str) -> str:
    for name, pat in CHANNEL_PATTERNS:
        if pat.search(narration):
            return name
    return "other"


def extract_refs(narration: str) -> dict:
    refs = {}
    for key, pat in (("vpa", RE_VPA), ("utr", RE_UTR_NEFT), ("ifsc", RE_IFSC),
                     ("gstin", RE_GSTIN), ("pan", RE_PAN), ("cheque", RE_CHQ)):
        m = pat.search(narration)
        if m:
            refs[key] = m.group(0) if key == "vpa" else m.group(1)
    if "utr" not in refs:
        m = RE_RRN.search(narration)
        if m:
            refs["rrn"] = m.group(1)
    return refs


def _clean_name(text: str) -> str:
    text = RE_MASKED.sub(" ", text)
    text = RE_NUMS.sub(" ", text)
    text = NOISE_TOKENS.sub(" ", text)
    text = re.sub(r"[/\-_.,:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def counterparty(narration: str, refs: dict) -> tuple[str, str]:
    """Return (key, display_name). VPA wins as the stable key when present."""
    if "vpa" in refs:
        vpa = refs["vpa"].lower()
        # name half of the narration, minus the vpa itself
        name = _clean_name(narration.replace(refs["vpa"], " "))
        return f"vpa:{vpa}", name.title() or vpa
    name = _clean_name(narration)
    # take the longest alpha token run as the name candidate
    words = [w for w in name.split() if len(w) > 2 and not w.isdigit()]
    display = " ".join(words[:4]).title()
    key = "name:" + re.sub(r"[^a-z0-9]", "", display.lower())[:40]
    return (key if len(key) > 5 else "", display)


def normalize_for_embedding(narration: str, refs: dict) -> str:
    text = narration
    for v in refs.values():
        text = text.replace(str(v), " ")
    return _clean_name(text).lower()


def enrich(narration: str) -> Enriched:
    refs = extract_refs(narration)
    channel = detect_channel(narration)
    key, name = counterparty(narration, refs)
    return Enriched(
        narration=narration, refs=refs, channel=channel,
        counterparty_key=key, counterparty_name=name,
        normalized=normalize_for_embedding(narration, refs),
    )
