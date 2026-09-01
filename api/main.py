"""Ledger Pilot API (M1) — the TRD §7 contract over the TRD §4 database.

Backed by Postgres (docker-compose.yml, port 5433) or SQLite, chosen by
DATABASE_URL. The HTTP contract did not change when the dict store was
replaced; that is what the test suite proves.

Run:  uvicorn api.main:app --port 8000   →  http://localhost:8000
"""
from __future__ import annotations

import io
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from bridge.tally_xml import undo_xml, voucher_xml
from db import store
from db.session import configure, session_scope
from engine.pipeline import classify_statement

app = FastAPI(title="Ledger Pilot")

# Firm-configurable guardrail: lines at or above this queue regardless of
# confidence. NOT the shipped calibrate.MATERIALITY_DEFAULT (₹50,000) — the demo
# client is a textile trader whose median line is ₹31k, and at ₹50,000 the
# guardrail alone would queue 37% of the statement. Every surface that reports
# an auto-rate must also report this number; see README.
MATERIALITY = 200_000.0

ACTOR = "priya"

# Parse previews are deliberately NOT persisted. Nothing about an uploaded file
# reaches the database until the operator has seen the parse and confirmed it —
# that is the whole point of the two-step upload.
PREVIEWS: dict = {}


def ensure_seeded():
    """Create the schema if needed and seed the two demo clients into an empty
    database. Idempotent — an existing database is left exactly as it is."""
    configure()
    if store.is_empty():
        store.seed_demo(MATERIALITY)


ensure_seeded()


def _404():
    raise HTTPException(404, "not found")


def _bank_configs():
    from engine.parsers import load_configs
    return load_configs()


# ── workspace ────────────────────────────────────────────────────────


@app.get("/api/clients")
def clients():
    with session_scope() as s:
        return store.list_clients(s)


@app.get("/api/statements/{sid}/queue")
def queue(sid: str):
    with session_scope() as s:
        return store.statement_payload(s, sid) or _404()


@app.get("/api/statements/{sid}/audit")
def audit(sid: str):
    """TRD §7: the audit trail is exportable per statement. Append-only."""
    with session_scope() as s:
        if s.get(store.Statement, sid) is None:
            _404()
        return {"statement_id": sid, "events": store.audit_trail(s, sid)}


# ── review ───────────────────────────────────────────────────────────


class ReviewIn(BaseModel):
    action: str                 # accept | reassign | suspense
    ledger_guid: str | None = None
    line_ids: list[str] | None = None   # for group review


@app.post("/api/lines/{lid}/review")
def review_line(lid: str, body: ReviewIn):
    sid = lid.rsplit("-", 1)[0]
    with session_scope() as s:
        if s.get(store.Statement, sid) is None or s.get(store.TransactionLine, lid) is None:
            _404()
        updated, proposals = store.apply_review(s, sid, [lid], body.action,
                                                body.ledger_guid, ACTOR)
        return {"line": updated[0] if updated else None,
                "rule_proposal": proposals[0] if proposals else None}


@app.post("/api/statements/{sid}/groups/review")
def review_group(sid: str, body: ReviewIn):
    """Atomic across the group: one decision, one transaction. A half-applied
    group is a worse outcome than a rejected one."""
    with session_scope() as s:
        payload = store.statement_payload(s, sid) or _404()
        live = {l["id"] for l in payload["lines"] if l["state"] == "queue"}
        ids = [i for i in (body.line_ids or []) if i in live]
        updated, proposals = store.apply_review(s, sid, ids, body.action,
                                                body.ledger_guid, ACTOR)
        return {"updated": len(updated), "rule_proposals": proposals}


# ── posting ──────────────────────────────────────────────────────────


def _postable(s, sid: str):
    st = s.get(store.Statement, sid) or _404()
    unreviewed = [l for l in st.lines if l.review_state == "queue"]
    if unreviewed:
        raise HTTPException(409, f"{len(unreviewed)} lines still need review")
    no_ledger = [l.id for l in st.lines if not l.final_ledger_guid]
    if no_ledger:
        raise HTTPException(409, f"{len(no_ledger)} lines have no final ledger: "
                                 f"{no_ledger[:5]}")
    try:
        return store.voucher_payload(s, sid)
    except KeyError as exc:
        orphan = exc.args[0]
        raise HTTPException(409, f"{len(orphan)} lines point at a ledger that is not in "
                                 f"the client's synced masters: {orphan[:5]}")


@app.post("/api/statements/{sid}/post")
def post_statement(sid: str):
    """Idempotent: a statement is posted under exactly one batch id, ever.

    A second call returns the first batch rather than minting a new one. Two
    batch ids for one statement means Tally can import every line twice under
    two different LP- refs, and undo_xml — which is keyed on the batch — can
    then only cancel one of them. Silent duplicates are the fastest way to lose
    a firm's trust (docs/decisions.md, the Hubdoc entry).
    """
    with session_scope() as s:
        st, lines, bank_ledger = _postable(s, sid)
        already = st.posted
        batch = st.batch_id if already else store.mark_posted(s, sid, len(lines))
        return {"batch_id": batch, "vouchers": len(lines),
                "xml": voucher_xml(lines, bank_ledger, batch),
                "already_posted": already}


@app.post("/api/statements/{sid}/undo")
def undo_statement(sid: str):
    """Cancellation XML for a posted batch. Import this into Tally *before*
    re-posting — the statement returns to unposted and the next post mints a
    fresh batch id."""
    with session_scope() as s:
        st = s.get(store.Statement, sid) or _404()
        if not st.posted:
            raise HTTPException(409, "statement is not posted; nothing to undo")
        n = len(st.lines)
        dates = {i: l.date for i, l in enumerate(st.lines, start=1)}
        batch = st.batch_id
        xml = undo_xml(batch, n, dates)
        store.mark_undone(s, sid)
        return {"batch_id": batch, "cancelled": n, "xml": xml}


@app.get("/api/statements/{sid}/xml", response_class=PlainTextResponse)
def get_xml(sid: str):
    """A read. It does not post, and it does not mint a batch id.

    XML only exists once a statement is posted. Handing out XML beforehand
    stamps it with a batch the server has not recorded, so importing that file
    puts vouchers into Tally that the undo path cannot reach.
    """
    with session_scope() as s:
        st = s.get(store.Statement, sid) or _404()
        if not st.posted:
            raise HTTPException(409, "statement is not posted yet — POST "
                                     f"/api/statements/{sid}/post to post it and "
                                     "receive its XML")
        _, lines, bank_ledger = store.voucher_payload(s, sid)
        return voucher_xml(lines, bank_ledger, st.batch_id)


# ── upload: parse preview, then classify ─────────────────────────────

SPREADSHEET = (".csv", ".txt", ".xlsx", ".xls")


def _read_upload(filename: str, blob: bytes):
    """Bytes → DataFrame, with the rejections the PRD asks to be graceful about."""
    import pandas as pd

    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        raise HTTPException(415, "PDF statements arrive in v1.5. For now, export "
                                 "CSV or XLSX from net-banking — same statement, "
                                 "no re-keying.")
    if suffix not in SPREADSHEET:
        raise HTTPException(415, f"Cannot read '{suffix or filename}'. Upload a CSV or "
                                 f"Excel bank statement ({', '.join(SPREADSHEET)}).")
    try:
        if suffix in (".xlsx", ".xls"):
            return pd.read_excel(io.BytesIO(blob), dtype=str)
        return pd.read_csv(io.BytesIO(blob), dtype=str)
    except Exception as exc:                       # unreadable / encrypted / not a table
        raise HTTPException(422, f"Could not read the file as a table. If it is "
                                 f"password-protected, remove the password and try "
                                 f"again. ({type(exc).__name__})")


@app.post("/api/clients/{cid}/upload")
async def upload(cid: str, file: UploadFile):
    """Step 1 of 2 — parse and check. Deliberately does NOT classify.

    The operator sees what we read out of their file, and the balance-continuity
    verdict, before a single line is sent anywhere. Returns a preview_id to hand
    to /classify. Nothing reaches the database in this step.
    """
    from engine.parsers import detect_bank, parse_dataframe

    with session_scope() as s:
        if s.get(store.Client, cid) is None:
            _404()

    df = _read_upload(file.filename, await file.read())
    cfg = detect_bank(list(df.columns))
    if cfg is None:
        raise HTTPException(422, "No bank format matched these columns: "
                                 f"{[str(x) for x in df.columns][:8]}. Supported: "
                                 f"{', '.join(sorted(x['bank'] for x in _bank_configs()))}.")
    stmt = parse_dataframe(df, cfg)
    if not stmt.lines:
        raise HTTPException(422, "Parsed 0 transaction rows — the file matched "
                                 f"{cfg['bank']} but every row was empty or unreadable.")

    pid = uuid.uuid4().hex[:8]
    PREVIEWS[pid] = {"client_id": cid, "stmt": stmt, "filename": file.filename,
                     "label": f"{cfg['bank']} · {file.filename}"}
    for stale in list(PREVIEWS)[:-20]:             # keep the store bounded
        PREVIEWS.pop(stale, None)

    dr = sum(l.amount for l in stmt.lines if l.direction == "dr")
    cr = sum(l.amount for l in stmt.lines if l.direction == "cr")
    dates = sorted(l.date for l in stmt.lines)
    return {
        "preview_id": pid, "bank": cfg["bank"], "filename": file.filename,
        "lines": len(stmt.lines), "from": dates[0], "to": dates[-1],
        "opening": stmt.opening_balance, "closing": stmt.closing_balance,
        "debits": round(dr, 2), "credits": round(cr, 2),
        "balance_ok": stmt.balance_ok,
        "balance_breaks": [
            {"row": i + 1, "date": stmt.lines[i].date, "narration": stmt.lines[i].narration,
             "amount": stmt.lines[i].amount, "direction": stmt.lines[i].direction,
             "balance": stmt.lines[i].balance}
            for i in stmt.balance_breaks[:10]],
        "sample": [{"date": l.date, "narration": l.narration, "amount": l.amount,
                    "direction": l.direction, "balance": l.balance}
                   for l in stmt.lines[:12]],
    }


@app.post("/api/clients/{cid}/previews/{pid}/classify")
def classify_preview(cid: str, pid: str):
    """Step 2 of 2 — the operator has seen the parse and confirmed it.

    A broken balance chain blocks this, per TRD §5: if the running balance does
    not reconcile we either mis-parsed the file or the PDF was edited, and
    either way classifying it would put invented numbers in front of a CA.
    """
    p = PREVIEWS.get(pid)
    if p is None or p["client_id"] != cid:
        _404()
    stmt = p["stmt"]
    if not stmt.balance_ok:
        raise HTTPException(409, f"Balance continuity fails at {len(stmt.balance_breaks)} "
                                 f"row(s): {[i + 1 for i in stmt.balance_breaks[:10]]}. "
                                 "The statement was mis-parsed or altered — nothing is "
                                 "classified until it reconciles.")
    with session_scope() as s:
        c = store.client_dict(s, cid) or _404()
        res = classify_statement(stmt, c["memory"], c["ledgers"],
                                 business=c["business"], calibration=c["calibration"],
                                 materiality=MATERIALITY)
        sid = store.store_statement(s, cid, res, p["label"], MATERIALITY,
                                    filename=p["filename"] or "")
        store.audit(s, actor=ACTOR, action="classify", client_id=cid, statement_id=sid,
                    detail={"file": p["filename"], "lines": len(res.lines)})
    PREVIEWS.pop(pid, None)
    return {"statement_id": sid, "lines": len(res.lines),
            "auto_rate": round(res.auto_rate, 3),
            "queued": sum(1 for l in res.lines if l.state == "queue")}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
