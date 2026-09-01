"""Ledger Pilot API (M1) — wraps the M0 engine behind the TRD §7 contract.

Demo mode: seeds two synthetic clients at startup (one warm with 6 months of
history, one cold day-zero client). State is in-memory for the demo; the data
model mirrors the TRD so Postgres slots in at M2 without reshaping.

Run:  uvicorn api.main:app --port 8000   →  http://localhost:8000
"""
from __future__ import annotations

import io
import sys
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from bridge.tally_xml import undo_xml, voucher_xml
from engine import calibrate
from engine.memory import ClientMemory
from engine.pipeline import Result, classify_statement
from harness.run import rows_to_stmt
from harness.synth import gen_month, history_vouchers, make_client

app = FastAPI(title="Ledger Pilot")

# ── in-memory demo store ─────────────────────────────────────────────
DB: dict = {"clients": {}, "statements": {}, "previews": {}, "audit": []}

# Firm-configurable guardrail: lines at or above this queue regardless of
# confidence. NOT the shipped calibrate.MATERIALITY_DEFAULT (₹50,000) — the demo
# client is a textile trader whose median line is ₹31k, and at ₹50,000 the
# guardrail alone would queue 37% of the statement. Every surface that reports
# an auto-rate must also report this number; see README.
MATERIALITY = 200_000.0


def _bank_configs():
    from engine.parsers import load_configs
    return load_configs()


def _client_record(cid, name, business, ledgers, memory, bank_ledger, tag=""):
    return {"id": cid, "name": name, "business": business, "ledgers": ledgers,
            "memory": memory, "bank_ledger": bank_ledger, "tag": tag,
            "calibration": [], "auto_history": []}


def _ledger_names(c):
    return {l["guid"]: l["name"] for l in c["ledgers"]}


def _store_statement(client, result: Result, label: str):
    sid = uuid.uuid4().hex[:8]
    lines = []
    names = _ledger_names(client)
    for i, l in enumerate(result.lines):
        s = l.suggestion
        lines.append({
            "id": f"{sid}-{i}", "date": l.date, "narration": l.narration,
            "amount": l.amount, "direction": l.direction, "channel": l.channel,
            "counterparty_key": l.counterparty_key, "counterparty_name": l.counterparty_name,
            "normalized": l.normalized,
            "suggestion": {
                "ledger_guid": s.ledger_guid,
                "ledger_name": names.get(s.ledger_guid, ""),
                "tier": s.tier, "score": round(s.raw_score, 3),
                "alternatives": [{"guid": g, "name": names.get(g, ""), "w": round(float(w), 2)}
                                 for g, w in s.alternatives if g in names][:3],
                "evidence": [e for e in s.evidence if e],
            },
            "state": "pre_approved" if l.state == "auto_approved" else "queue",
            "final_guid": s.ledger_guid if l.state == "auto_approved" else None,
            "reviewed_by": None,
        })
    DB["statements"][sid] = {
        "id": sid, "client_id": client["id"], "label": label,
        "bank": result.statement.bank, "balance_ok": result.statement.balance_ok,
        "opening": result.statement.opening_balance,
        "closing": result.statement.closing_balance,
        "threshold": round(result.threshold.value, 2),
        "calibrated": result.threshold.calibrated,
        "lines": lines, "posted": False, "batch_id": None,
    }
    return sid


def seed():
    # warm client: Sharma Textiles with 6 months of Tally history
    c = make_client()
    mem = ClientMemory(c.client_id)
    mem.bootstrap_from_history(history_vouchers(c, [f"2026-{m:02d}" for m in range(2, 8)]))
    warm = _client_record("sharma", "Sharma Textiles Pvt Ltd", c.business,
                          c.ledgers, mem, c.bank_ledger, tag="6 months history")
    calibration = []
    for k, mth in enumerate(["2026-05", "2026-06", "2026-07", "2026-04", "2026-03"]):
        rows, truth = gen_month(c, mth, seed=555 + k)
        res = classify_statement(rows_to_stmt(rows), mem, c.ledgers, business=c.business,
                                 materiality=MATERIALITY)
        calibration += [(l.suggestion.raw_score, l.suggestion.ledger_guid == t)
                        for l, t in zip(res.lines, truth) if l.suggestion.ledger_guid]
    warm["calibration"] = calibration
    DB["clients"]["sharma"] = warm
    rows, _ = gen_month(c, "2026-08", seed=999)
    res = classify_statement(rows_to_stmt(rows), mem, c.ledgers, business=c.business,
                             calibration=calibration, materiality=MATERIALITY)
    _store_statement(warm, res, "HDFC ···5521 · Aug 2026")

    # cold client: day zero, masters only
    c2 = make_client(seed=42)
    cold = _client_record("deshmukh", "Deshmukh Auto Spares", "auto spare parts trading, Pune",
                          c2.ledgers, ClientMemory("deshmukh"), c2.bank_ledger,
                          tag="new client · cold start")
    DB["clients"]["deshmukh"] = cold
    rows2, _ = gen_month(c2, "2026-08", seed=77)
    res2 = classify_statement(rows_to_stmt(rows2), cold["memory"], c2.ledgers,
                              business=cold["business"], materiality=MATERIALITY)
    _store_statement(cold, res2, "Axis ···3318 · Aug 2026")


seed()

# ── API ──────────────────────────────────────────────────────────────


@app.get("/api/clients")
def clients():
    out = []
    for c in DB["clients"].values():
        stmts = [s for s in DB["statements"].values() if s["client_id"] == c["id"]]
        pending = sum(1 for s in stmts for l in s["lines"] if l["state"] == "queue")
        total = sum(len(s["lines"]) for s in stmts) or 1
        auto = sum(1 for s in stmts for l in s["lines"] if l["state"] != "queue")
        out.append({"id": c["id"], "name": c["name"], "business": c["business"],
                    "tag": c["tag"], "pending": pending,
                    "auto_rate": round(auto / total, 3),
                    "statements": [{"id": s["id"], "label": s["label"],
                                    "posted": s["posted"]} for s in stmts]})
    return out


@app.get("/api/statements/{sid}/queue")
def queue(sid: str):
    s = DB["statements"].get(sid) or _404()
    c = DB["clients"][s["client_id"]]
    q = [l for l in s["lines"] if l["state"] == "queue"]
    groups = defaultdict(list)
    for l in q:
        groups[l["counterparty_key"] or l["id"]].append(l["id"])
    grouped = [{"key": k, "line_ids": ids,
                "name": next(x["counterparty_name"] for x in q if x["id"] in ids),
                "suggested": next(x["suggestion"] for x in q if x["id"] in ids)}
               for k, ids in groups.items() if len(ids) > 1]
    ready = [l for l in s["lines"] if l["state"] in ("pre_approved", "human_reviewed")]
    return {"statement": {k: s[k] for k in ("id", "label", "bank", "balance_ok",
                                            "opening", "closing", "threshold",
                                            "calibrated", "posted")},
            "client": {"id": c["id"], "name": c["name"], "tag": c["tag"]},
            "ledgers": [{"guid": l["guid"], "name": l["name"]} for l in c["ledgers"]],
            "lines": s["lines"], "groups": grouped,
            "materiality": MATERIALITY,
            "ready": {"count": len(ready), "sum": round(sum(l["amount"] for l in ready), 2)}}


class ReviewIn(BaseModel):
    action: str                 # accept | reassign | suspense
    ledger_guid: str | None = None
    line_ids: list[str] | None = None   # for group review


def _apply_review(s, line, action, guid, c):
    suspense = next(l["guid"] for l in c["ledgers"] if "Suspense" in l["name"])
    if action == "accept":
        line["final_guid"] = line["suggestion"]["ledger_guid"] or suspense
    elif action == "reassign" and guid:
        line["final_guid"] = guid
    else:
        line["final_guid"] = suspense
    line["state"] = "human_reviewed"
    line["reviewed_by"] = "priya"
    correct = action == "accept"
    c["calibration"].append((line["suggestion"]["score"], correct))
    proposal = c["memory"].learn(line["counterparty_key"], line["normalized"],
                                 line["final_guid"])
    DB["audit"].append({"actor": "priya", "action": action, "line": line["id"]})
    return proposal


@app.post("/api/lines/{lid}/review")
def review_line(lid: str, body: ReviewIn):
    sid = lid.rsplit("-", 1)[0]
    s = DB["statements"].get(sid) or _404()
    c = DB["clients"][s["client_id"]]
    line = next((l for l in s["lines"] if l["id"] == lid), None) or _404()
    proposal = _apply_review(s, line, body.action, body.ledger_guid, c)
    return {"line": line, "rule_proposal": proposal}


@app.post("/api/statements/{sid}/groups/review")
def review_group(sid: str, body: ReviewIn):
    s = DB["statements"].get(sid) or _404()
    c = DB["clients"][s["client_id"]]
    updated = []
    for lid in body.line_ids or []:
        line = next((l for l in s["lines"] if l["id"] == lid), None)
        if line and line["state"] == "queue":
            _apply_review(s, line, body.action, body.ledger_guid, c)
            updated.append(line)
    return {"updated": len(updated)}


def _voucher_lines(s, c):
    """The posting payload for a statement. Pure — reads, never writes."""
    names = _ledger_names(c)
    orphan = [l["id"] for l in s["lines"] if l["final_guid"] not in names]
    if orphan:
        raise HTTPException(409, f"{len(orphan)} lines point at a ledger that is not in "
                                 f"the client's synced masters: {orphan[:5]}")
    return [{"date": l["date"], "narration": l["narration"], "amount": l["amount"],
             "direction": l["direction"], "ledger_name": names[l["final_guid"]]}
            for l in s["lines"]]


def _assert_postable(s):
    unreviewed = [l for l in s["lines"] if l["state"] == "queue"]
    if unreviewed:
        raise HTTPException(409, f"{len(unreviewed)} lines still need review")
    no_ledger = [l["id"] for l in s["lines"] if not l["final_guid"]]
    if no_ledger:
        raise HTTPException(409, f"{len(no_ledger)} lines have no final ledger: {no_ledger[:5]}")


@app.post("/api/statements/{sid}/post")
def post_statement(sid: str):
    """Idempotent: a statement is posted under exactly one batch id, ever.

    A second call returns the first batch rather than minting a new one. Two
    batch ids for one statement means Tally can import every line twice under
    two different LP- refs, and undo_xml — which is keyed on the batch — can
    then only cancel one of them. Silent duplicates are the fastest way to lose
    a firm's trust (docs/decisions.md, the Hubdoc entry).
    """
    s = DB["statements"].get(sid) or _404()
    c = DB["clients"][s["client_id"]]
    _assert_postable(s)
    lines = _voucher_lines(s, c)
    already = s["posted"]
    if not already:
        s["posted"], s["batch_id"] = True, uuid.uuid4().hex[:6]
        DB["audit"].append({"actor": "priya", "action": "post",
                            "statement": sid, "batch": s["batch_id"]})
    return {"batch_id": s["batch_id"], "vouchers": len(lines),
            "xml": voucher_xml(lines, c["bank_ledger"], s["batch_id"]),
            "already_posted": already}


@app.post("/api/statements/{sid}/undo")
def undo_statement(sid: str):
    """Cancellation XML for a posted batch. Import this into Tally *before*
    re-posting — the statement returns to unposted and the next post mints a
    fresh batch id."""
    s = DB["statements"].get(sid) or _404()
    if not s["posted"]:
        raise HTTPException(409, "statement is not posted; nothing to undo")
    batch, n = s["batch_id"], len(s["lines"])
    xml = undo_xml(batch, n, {i: l["date"] for i, l in enumerate(s["lines"], start=1)})
    s["undone"] = s.get("undone", []) + [batch]
    s["posted"], s["batch_id"] = False, None
    DB["audit"].append({"actor": "priya", "action": "undo",
                        "statement": sid, "batch": batch})
    return {"batch_id": batch, "cancelled": n, "xml": xml}


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
    to /classify.
    """
    c = DB["clients"].get(cid) or _404()
    from engine.parsers import detect_bank, parse_dataframe

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
    DB["previews"][pid] = {"client_id": cid, "stmt": stmt, "filename": file.filename,
                           "label": f"{cfg['bank']} · {file.filename}"}
    for stale in list(DB["previews"])[:-20]:       # keep the store bounded
        DB["previews"].pop(stale, None)

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
    c = DB["clients"].get(cid) or _404()
    p = DB["previews"].get(pid) or _404()
    if p["client_id"] != cid:
        raise HTTPException(404, "not found")
    stmt = p["stmt"]
    if not stmt.balance_ok:
        raise HTTPException(409, f"Balance continuity fails at {len(stmt.balance_breaks)} "
                                 f"row(s): {[i + 1 for i in stmt.balance_breaks[:10]]}. "
                                 "The statement was mis-parsed or altered — nothing is "
                                 "classified until it reconciles.")
    res = classify_statement(stmt, c["memory"], c["ledgers"], business=c["business"],
                             calibration=c["calibration"], materiality=MATERIALITY)
    sid = _store_statement(c, res, p["label"])
    DB["previews"].pop(pid, None)
    DB["audit"].append({"actor": "priya", "action": "classify",
                        "statement": sid, "file": p["filename"]})
    return {"statement_id": sid, "lines": len(res.lines),
            "auto_rate": round(res.auto_rate, 3),
            "queued": sum(1 for l in res.lines if l.state == "queue")}


@app.get("/api/statements/{sid}/xml", response_class=PlainTextResponse)
def get_xml(sid: str):
    """A read. It does not post, and it does not mint a batch id.

    XML only exists once a statement is posted. Handing out XML beforehand
    stamps it with a batch the server has not recorded, so importing that file
    puts vouchers into Tally that the undo path cannot reach.
    """
    s = DB["statements"].get(sid) or _404()
    if not s["posted"]:
        raise HTTPException(409, "statement is not posted yet — POST "
                                 f"/api/statements/{sid}/post to post it and receive its XML")
    c = DB["clients"][s["client_id"]]
    return voucher_xml(_voucher_lines(s, c), c["bank_ledger"], s["batch_id"])


def _404():
    raise HTTPException(404, "not found")


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
