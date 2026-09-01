"""Tally voucher + masters XML generation (TallyPrime import schema).

Idempotency: every voucher narration carries `LP-{batch}-{seq}` — the duplicate
key that survives even file-based import. Undo: a companion XML of Cancelled
voucher messages by the same refs.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

VOUCHER_TYPE = {"dr": "Payment", "cr": "Receipt", "contra": "Contra"}


def _envelope(body: str, report: str = "Vouchers") -> str:
    return (
        '<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>'
        '<BODY><IMPORTDATA><REQUESTDESC>'
        f'<REPORTNAME>{report}</REPORTNAME>'
        '<STATICVARIABLES><SVCURRENTCOMPANY></SVCURRENTCOMPANY></STATICVARIABLES>'
        '</REQUESTDESC><REQUESTDATA>'
        f'{body}'
        '</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>'
    )


def _amt(x: float) -> str:
    return f"{x:.2f}"


def voucher_xml(lines: list[dict], bank_ledger: str, batch_id: str) -> str:
    """lines: [{date: 'yyyy-mm-dd', narration, amount, direction, ledger_name}].
    Payment (dr): debit target ledger, credit bank. Receipt (cr): the reverse."""
    msgs = []
    for seq, l in enumerate(lines, start=1):
        ref = f"LP-{batch_id}-{seq:04d}"
        vtype = VOUCHER_TYPE.get(l["direction"], "Journal")
        date = l["date"].replace("-", "")
        narration = escape(f"{l['narration']} [{ref}]")
        target = escape(l["ledger_name"]); bank = escape(bank_ledger)
        amt = l["amount"]
        if l["direction"] == "dr":     # money out: Dr target / Cr bank
            entries = (
                f'<ALLLEDGERENTRIES.LIST><LEDGERNAME>{target}</LEDGERNAME>'
                f'<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{_amt(amt)}</AMOUNT>'
                f'</ALLLEDGERENTRIES.LIST>'
                f'<ALLLEDGERENTRIES.LIST><LEDGERNAME>{bank}</LEDGERNAME>'
                f'<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{_amt(amt)}</AMOUNT>'
                f'</ALLLEDGERENTRIES.LIST>')
        else:                          # money in: Dr bank / Cr target
            entries = (
                f'<ALLLEDGERENTRIES.LIST><LEDGERNAME>{bank}</LEDGERNAME>'
                f'<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{_amt(amt)}</AMOUNT>'
                f'</ALLLEDGERENTRIES.LIST>'
                f'<ALLLEDGERENTRIES.LIST><LEDGERNAME>{target}</LEDGERNAME>'
                f'<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{_amt(amt)}</AMOUNT>'
                f'</ALLLEDGERENTRIES.LIST>')
        msgs.append(
            f'<TALLYMESSAGE xmlns:UDF="TallyUDF">'
            f'<VOUCHER VCHTYPE="{vtype}" ACTION="Create">'
            f'<DATE>{date}</DATE><VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME>'
            f'<NARRATION>{narration}</NARRATION>'
            f'<VOUCHERNUMBER>{ref}</VOUCHERNUMBER>'
            f'{entries}</VOUCHER></TALLYMESSAGE>')
    return _envelope("".join(msgs))


def masters_xml(ledgers: list[dict]) -> str:
    """ledgers: [{name, parent}] — new ledgers approved from proposals."""
    msgs = [
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF">'
        f'<LEDGER NAME="{escape(l["name"])}" ACTION="Create">'
        f'<NAME>{escape(l["name"])}</NAME><PARENT>{escape(l["parent"])}</PARENT>'
        f'</LEDGER></TALLYMESSAGE>'
        for l in ledgers]
    return _envelope("".join(msgs), report="All Masters")


def undo_xml(batch_id: str, count: int, date_by_seq: dict[int, str]) -> str:
    """Cancellation messages for every voucher ref in a posted batch."""
    msgs = []
    for seq in range(1, count + 1):
        ref = f"LP-{batch_id}-{seq:04d}"
        date = date_by_seq.get(seq, "").replace("-", "")
        msgs.append(
            f'<TALLYMESSAGE xmlns:UDF="TallyUDF">'
            f'<VOUCHER ACTION="Cancel"><DATE>{date}</DATE>'
            f'<VOUCHERNUMBER>{ref}</VOUCHERNUMBER></VOUCHER></TALLYMESSAGE>')
    return _envelope("".join(msgs))
