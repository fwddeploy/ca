"""Statement parsing: bank-config-driven CSV/Excel (v1 core) with balance validation.

Each bank config (parsers/banks/*.yaml) declares header signatures and column maps.
PDF parsing (pdfplumber) reuses the same configs once table rows are extracted; the
synthetic harness and most CA-firm exports are CSV/XLS, so that path is primary.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

BANKS_DIR = Path(__file__).parent / "banks"


@dataclass
class ParsedLine:
    date: str          # ISO yyyy-mm-dd
    narration: str
    amount: float      # always positive
    direction: str     # "dr" | "cr"
    balance: float | None


@dataclass
class ParsedStatement:
    bank: str
    lines: list
    opening_balance: float | None
    closing_balance: float | None
    balance_ok: bool
    balance_breaks: list  # row indices where the running chain breaks


def load_configs() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(BANKS_DIR.glob("*.yaml"))]


def detect_bank(columns: list[str]) -> dict | None:
    cols = [c.strip().lower() for c in columns]
    for cfg in load_configs():
        sig = [s.lower() for s in cfg["signature_columns"]]
        if all(any(s in c for c in cols) for s in sig):
            return cfg
    return None


def _col(df: pd.DataFrame, name_options: list[str]) -> str | None:
    for c in df.columns:
        cl = str(c).strip().lower()
        if any(opt in cl for opt in name_options):
            return c
    return None


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).replace(",", "").replace("₹", "").strip()
    if s in ("", "-", "—", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(v, fmts: list[str]) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v).strip()
    for f in fmts:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return s  # leave as-is; validation happens downstream


def parse_dataframe(df: pd.DataFrame, cfg: dict) -> ParsedStatement:
    m = cfg["columns"]
    fmts = cfg.get("date_formats", ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y-%m-%d"])
    date_c = _col(df, m["date"]); narr_c = _col(df, m["narration"])
    bal_c = _col(df, m.get("balance", ["balance"]))
    wd_c = _col(df, m.get("withdrawal", [])); dep_c = _col(df, m.get("deposit", []))
    amt_c = _col(df, m.get("amount", [])); typ_c = _col(df, m.get("drcr", []))

    lines: list[ParsedLine] = []
    for _, row in df.iterrows():
        narr = str(row.get(narr_c, "")).strip()
        if not narr or narr.lower() == "nan":
            continue
        bal = _num(row.get(bal_c)) if bal_c else None
        if wd_c or dep_c:
            wd = _num(row.get(wd_c)) if wd_c else None
            dep = _num(row.get(dep_c)) if dep_c else None
            if wd:
                amount, direction = wd, "dr"
            elif dep:
                amount, direction = dep, "cr"
            else:
                continue
        else:
            amount = _num(row.get(amt_c))
            if amount is None:
                continue
            drcr = str(row.get(typ_c, "")).strip().lower()
            direction = "dr" if ("dr" in drcr or amount < 0) else "cr"
            amount = abs(amount)
        lines.append(ParsedLine(_date(row.get(date_c), fmts), narr, round(amount, 2), direction, bal))

    opening, closing, ok, breaks = validate_balance(lines)
    return ParsedStatement(cfg["bank"], lines, opening, closing, ok, breaks)


def validate_balance(lines: list) -> tuple[float | None, float | None, bool, list]:
    """Opening + Σ(txns) must equal closing, checked row by row where balances exist."""
    with_bal = [(i, l) for i, l in enumerate(lines) if l.balance is not None]
    if len(with_bal) < 2:
        return None, None, len(lines) > 0, []
    breaks = []
    first_i, first = with_bal[0]
    delta0 = first.amount if first.direction == "cr" else -first.amount
    opening = round(first.balance - delta0, 2)
    prev_bal = first.balance
    for i, line in with_bal[1:]:
        delta = line.amount if line.direction == "cr" else -line.amount
        expected = round(prev_bal + delta, 2)
        if abs(expected - line.balance) > 0.01:
            breaks.append(i)
            prev_bal = line.balance  # resync so one break doesn't cascade
        else:
            prev_bal = expected
    return opening, with_bal[-1][1].balance, not breaks, breaks


def parse_file(path: str | Path) -> ParsedStatement:
    path = Path(path)
    if path.suffix.lower() in (".csv", ".txt"):
        df = pd.read_csv(path, dtype=str)
    elif path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str)
    else:
        raise ValueError(f"v1 parses CSV/Excel; got {path.suffix}. "
                         "Scanned/PDF statements need the PDF pipeline (v1.5).")
    cfg = detect_bank(list(df.columns))
    if cfg is None:
        raise ValueError("Unknown statement format — no bank config matched the columns: "
                         f"{list(df.columns)}")
    return parse_dataframe(df, cfg)
