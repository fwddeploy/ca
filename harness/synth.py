"""Synthetic golden-set generator.

Stand-in for real design-partner data: generates a client with a realistic
Indian chart of ledgers, months of 'posted history' (the Tally export), and a
test statement where every line carries its TRUE ledger — so the harness can
score the engine exactly the way it will score against real books.
Replace with real Tally exports the day the CA team hands them over; the
harness interface does not change.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

BANKS = ["ybl", "okhdfcbank", "oksbi", "paytm", "ibl", "axl"]


def _vpa(name: str, rng) -> str:
    stem = "".join(name.lower().split())[:12]
    return f"{stem}{rng.randint(1, 99)}@{rng.choice(BANKS)}"


@dataclass
class Party:
    name: str
    ledger: str           # ledger name
    kind: str             # upi | neft | imps | cheque | ach
    amount_range: tuple
    freq: int             # ~occurrences per month
    vpa: str = ""


@dataclass
class SynthClient:
    client_id: str
    business: str
    ledgers: list = field(default_factory=list)     # [{guid, name, parent}]
    parties: list = field(default_factory=list)
    bank_ledger: str = "HDFC Bank A/c 5521"

    def guid_of(self, name: str) -> str:
        return next(l["guid"] for l in self.ledgers if l["name"] == name)


def make_client(seed: int = 7) -> SynthClient:
    rng = random.Random(seed)
    ledger_names = [
        ("Purchases - Yarn", "Purchase Accounts"),
        ("Purchases - Fabric", "Purchase Accounts"),
        ("Job Work Charges - Dyeing", "Direct Expenses"),
        ("Freight & Cartage Inward", "Direct Expenses"),
        ("Sales - Fabric", "Sales Accounts"),
        ("Salary & Wages", "Indirect Expenses"),
        ("Staff Welfare Expenses", "Indirect Expenses"),
        ("Shop Rent", "Indirect Expenses"),
        ("Electricity Expenses", "Indirect Expenses"),
        ("Telephone & Internet", "Indirect Expenses"),
        ("Fuel & Conveyance", "Indirect Expenses"),
        ("Insurance Premium", "Indirect Expenses"),
        ("Bank Charges", "Indirect Expenses"),
        ("Interest on OD", "Indirect Expenses"),
        ("GST Payable", "Duties & Taxes"),
        ("TDS Payable", "Duties & Taxes"),
        ("Loan EMI - Bajaj Finance", "Loans (Liability)"),
        ("Cash", "Cash-in-Hand"),
        ("Suspense", "Suspense A/c"),
    ]
    client = SynthClient("sharma_textiles", "textile trading and processing, Surat")
    client.ledgers = [{"guid": f"g-{i:03d}", "name": n, "parent": p}
                      for i, (n, p) in enumerate(ledger_names)]

    def P(name, ledger, kind, lo, hi, freq):
        client.parties.append(Party(name, ledger, kind, (lo, hi), freq,
                                    vpa=_vpa(name, rng) if kind == "upi" else ""))

    # suppliers / expenses (dr)
    P("RAMESH TRADERS", "Purchases - Yarn", "upi", 8000, 60000, 4)
    P("GUPTA YARN AGENCY", "Purchases - Yarn", "cheque", 40000, 150000, 2)
    P("KAVERI FABRICS", "Purchases - Fabric", "neft", 25000, 120000, 3)
    P("GANGA DYEING WORKS", "Job Work Charges - Dyeing", "imps", 30000, 80000, 3)
    P("SHREE ROADWAYS TRANSPORT", "Freight & Cartage Inward", "upi", 2000, 12000, 4)
    P("SWIGGY LTD", "Staff Welfare Expenses", "upi", 300, 1800, 6)
    P("MANOJ BHAI LANDLORD", "Shop Rent", "neft", 42000, 42000, 1)
    P("DGVCL POWER", "Electricity Expenses", "ach", 6000, 18000, 1)
    P("AIRTEL BROADBAND", "Telephone & Internet", "ach", 1200, 2400, 1)
    P("HP PETROL PUMP SURAT", "Fuel & Conveyance", "upi", 500, 4000, 5)
    P("HDFC ERGO GIC LTD", "Insurance Premium", "ach", 8412, 8412, 1)
    P("BAJAJ FINANCE LTD", "Loan EMI - Bajaj Finance", "ach", 21350, 21350, 1)
    # customers (cr)
    P("KUMAR RETAIL", "Sales - Fabric", "upi", 15000, 90000, 4)
    P("SURAT SAREE HOUSE", "Sales - Fabric", "neft", 40000, 200000, 3)
    P("BOMBAY TEXTILE MART", "Sales - Fabric", "imps", 30000, 150000, 2)
    return client


def _narration(p: Party, rng, direction: str) -> str:
    rrn = rng.randint(10**11, 10**12 - 1)
    if p.kind == "upi":
        return f"UPI/{'DR' if direction == 'dr' else 'CR'}/{rrn}/{p.name}/{p.vpa.split('@')[1].upper()}/{p.vpa}"
    if p.kind == "neft":
        utr = f"HDFCN{rng.randint(10**10, 10**11 - 1)}"
        return f"NEFT {'DR' if direction == 'dr' else 'CR'}-{p.name}-{utr}"
    if p.kind == "imps":
        return f"IMPS-{rrn}-{p.name}-KKBK-xxxx{rng.randint(1000, 9999)}"
    if p.kind == "cheque":
        return f"CHQ PAID-MICR CTS-{rng.randint(100000, 999999)}-{p.name}"
    return f"ACH D- {p.name}-{rng.randint(10**8, 10**9 - 1)}"


def gen_month(client: SynthClient, month: str, seed: int,
              include_oddballs: bool = True):
    """→ (statement_rows, truth) where truth[i] = true ledger guid per row."""
    rng = random.Random(seed)
    rows, truth = [], []

    def add(day, narration, amount, direction, ledger):
        rows.append({"day": day, "narration": narration,
                     "amount": round(amount, 2), "direction": direction})
        truth.append(client.guid_of(ledger))

    for p in client.parties:
        direction = "cr" if p.ledger.startswith("Sales") else "dr"
        for _ in range(max(1, int(rng.gauss(p.freq, 1)))):
            amt = rng.uniform(*p.amount_range)
            add(rng.randint(1, 28), _narration(p, rng, direction), amt, direction, p.ledger)

    # structural lines every month
    add(1, f"SALARY AUG-BATCH 01-{rng.randint(18, 25)} CREDITS", rng.uniform(180000, 300000), "dr", "Salary & Wages")
    add(4, "CONSOLIDATED CHARGES FOR A/C + GST", rng.uniform(500, 1200), "dr", "Bank Charges")
    add(20, f"GST PMT-CBIC-{rng.randint(10**8, 10**9)}", rng.uniform(40000, 140000), "dr", "GST Payable")
    add(7, f"TDS CHALLAN OLTAS CBDT {rng.randint(10**6, 10**7)}", rng.uniform(8000, 30000), "dr", "TDS Payable")
    add(28, "INT.COLL 5521 OD", rng.uniform(2000, 9000), "dr", "Interest on OD")

    if include_oddballs:   # lines that should NOT auto-post
        add(14, f"NEFT DR-ANIL KUMAR-SBIN0004432-{rng.randint(10**6, 10**7)}",
            300000, "dr", "Suspense")
        add(22, f"CASH DEP-BRANCH SURAT-REF {rng.randint(10**4, 10**5)}",
            rng.uniform(50000, 90000), "cr", "Cash")
        newp = Party("OM SHAKTI ENTERPRISES", "Purchases - Fabric", "upi",
                     (20000, 30000), 1, vpa=_vpa("OM SHAKTI", rng))
        add(9, _narration(newp, rng, "dr"), rng.uniform(*newp.amount_range), "dr", newp.ledger)

    order = sorted(range(len(rows)), key=lambda i: rows[i]["day"])
    rows = [rows[i] for i in order]; truth = [truth[i] for i in order]

    # running balance + dates
    bal = 482110.50
    for r in rows:
        bal += r["amount"] if r["direction"] == "cr" else -r["amount"]
        r["balance"] = round(bal, 2)
        r["date"] = f"{month}-{r.pop('day'):02d}"
    return rows, truth


def to_hdfc_csv(rows: list[dict], path: str):
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Narration", "Withdrawal", "Deposit", "Closing Balance"])
        for r in rows:
            d = r["date"].split("-")
            w.writerow([f"{d[2]}/{d[1]}/{d[0][2:]}", r["narration"],
                        r["amount"] if r["direction"] == "dr" else "",
                        r["amount"] if r["direction"] == "cr" else "",
                        r["balance"]])


def history_vouchers(client: SynthClient, months: list[str], seed0: int = 100):
    """Simulate the Tally export: past months' lines with their true ledgers,
    in the shape ClientMemory.bootstrap_from_history expects."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from engine.enrich import enrich
    vouchers = []
    for k, month in enumerate(months):
        rows, truth = gen_month(client, month, seed0 + k, include_oddballs=False)
        for r, guid in zip(rows, truth):
            e = enrich(r["narration"])
            vouchers.append({"narration_norm": e.normalized,
                             "counterparty_key": e.counterparty_key,
                             "ledger_guid": guid})
    return vouchers
