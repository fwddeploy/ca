"""The repository the API talks to.

Deliberately returns the same shapes `api/main.py` already returned from its
dict store, so swapping the backend is provable: the existing API tests pass
unchanged against a real database, or the swap was not faithful.

Money crosses this boundary as `float`, because the engine is float and always
has been. Exact `Decimal` lives on the database side of the line, here and
nowhere else.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select

from engine.memory import ClientMemory, Rule as EngineRule

from .models import (AuditEvent, CalibrationPoint, Client, Firm, LabeledLine,
                     LedgerMaster, MemoryPattern, Rule, Statement,
                     TransactionLine, VoucherBatch)
from .session import session_scope

FIRM_ID = "mehta"


def _f(x) -> float | None:
    """Decimal → float at the boundary, and only here."""
    return None if x is None else float(x)


def _now():
    return datetime.now(timezone.utc)


# ── clients ──────────────────────────────────────────────────────────

def ledgers_of(s, cid: str) -> list[dict]:
    rows = s.scalars(select(LedgerMaster).where(LedgerMaster.client_id == cid)
                     .order_by(LedgerMaster.id)).all()
    return [{"guid": r.guid, "name": r.name, "parent": r.parent,
             "gloss": r.gloss, "category_id": r.category_id} for r in rows]


def ledger_names(s, cid: str) -> dict[str, str]:
    return dict(s.execute(select(LedgerMaster.guid, LedgerMaster.name)
                          .where(LedgerMaster.client_id == cid)).all())


def load_memory(s, cid: str) -> ClientMemory:
    """Rebuild the engine's ClientMemory from the tables. Rules, counterparty
    patterns and the labeled corpus — the whole of T0, T1 and T2's input."""
    mem = ClientMemory(cid)
    mem.rules = [EngineRule(r.pattern, r.match_type, r.ledger_guid, r.scope, r.created_from)
                 for r in s.scalars(select(Rule).where(Rule.client_id == cid))]
    for p in s.scalars(select(MemoryPattern).where(MemoryPattern.client_id == cid)):
        mem.patterns.setdefault(p.counterparty_key, {})[p.ledger_guid] = p.count
    mem.labeled = [(r.narration_norm, r.ledger_guid) for r in
                   s.scalars(select(LabeledLine).where(LabeledLine.client_id == cid)
                             .order_by(LabeledLine.id))]
    return mem


def calibration_of(s, cid: str) -> list[tuple[float, bool]]:
    return [(p.raw_score, p.was_correct) for p in
            s.scalars(select(CalibrationPoint).where(CalibrationPoint.client_id == cid))]


def client_dict(s, cid: str) -> dict | None:
    c = s.get(Client, cid)
    if c is None:
        return None
    return {"id": c.id, "name": c.name, "business": c.business, "tag": c.tag,
            "bank_ledger": c.bank_ledger, "ledgers": ledgers_of(s, cid),
            "memory": load_memory(s, cid), "calibration": calibration_of(s, cid)}


def list_clients(s) -> list[dict]:
    out = []
    for c in s.scalars(select(Client).order_by(Client.created_at, Client.id)):
        stmts = s.scalars(select(Statement).where(Statement.client_id == c.id)
                          .order_by(Statement.created_at, Statement.id)).all()
        ids = [x.id for x in stmts]
        pending = total = auto = 0
        if ids:
            pending = s.scalar(select(func.count()).select_from(TransactionLine)
                               .where(TransactionLine.statement_id.in_(ids),
                                      TransactionLine.review_state == "queue")) or 0
            total = s.scalar(select(func.count()).select_from(TransactionLine)
                             .where(TransactionLine.statement_id.in_(ids))) or 0
            auto = total - pending
        out.append({"id": c.id, "name": c.name, "business": c.business, "tag": c.tag,
                    "pending": pending, "auto_rate": round(auto / (total or 1), 3),
                    "statements": [{"id": x.id, "label": x.label, "posted": x.posted}
                                   for x in stmts]})
    return out


# ── statements ───────────────────────────────────────────────────────

def _line_dict(l: TransactionLine, names: dict[str, str]) -> dict:
    sug = dict(l.suggestion or {})
    sug["ledger_name"] = names.get(sug.get("ledger_guid") or "", "")
    sug["alternatives"] = [{"guid": a["guid"], "name": names.get(a["guid"], ""),
                            "w": a.get("w", 0)}
                           for a in sug.get("alternatives", []) if a["guid"] in names][:3]
    return {"id": l.id, "date": l.date, "narration": l.narration_raw,
            "amount": _f(l.amount), "direction": l.direction, "channel": l.channel,
            "counterparty_key": l.counterparty_key,
            "counterparty_name": l.counterparty_name,
            "normalized": l.narration_norm, "suggestion": sug,
            "state": l.review_state, "final_guid": l.final_ledger_guid,
            "reviewed_by": l.reviewed_by}


def store_statement(s, cid: str, result, label: str, materiality: float,
                    filename: str = "") -> str:
    """Persist a classified statement. Mirrors the old _store_statement()."""
    sid = uuid.uuid4().hex[:8]
    st = result.statement
    s.add(Statement(
        id=sid, client_id=cid, label=label, bank=st.bank, filename=filename,
        opening_balance=st.opening_balance, closing_balance=st.closing_balance,
        balance_ok=st.balance_ok, balance_breaks=list(st.balance_breaks),
        threshold=round(result.threshold.value, 2),
        calibrated=result.threshold.calibrated, materiality=Decimal(str(materiality)),
        posted=False, batch_id=None))
    s.flush()
    for i, l in enumerate(result.lines):
        sug = l.suggestion
        auto = l.state == "auto_approved"
        s.add(TransactionLine(
            id=f"{sid}-{i}", statement_id=sid, seq=i, date=l.date,
            narration_raw=l.narration, narration_norm=l.normalized,
            amount=Decimal(str(l.amount)), direction=l.direction,
            balance=None, channel=l.channel,
            refs={}, counterparty_key=l.counterparty_key,
            counterparty_name=l.counterparty_name,
            suggestion={"ledger_guid": sug.ledger_guid, "tier": sug.tier,
                        "score": round(sug.raw_score, 3),
                        "category_id": sug.category_id,
                        "alternatives": [{"guid": g, "w": round(float(w), 2)}
                                         for g, w in sug.alternatives],
                        "evidence": [e for e in sug.evidence if e]},
            review_state="pre_approved" if auto else "queue",
            final_ledger_guid=sug.ledger_guid if auto else None))
    return sid


def statement_payload(s, sid: str) -> dict | None:
    st = s.get(Statement, sid)
    if st is None:
        return None
    names = ledger_names(s, st.client_id)
    c = s.get(Client, st.client_id)
    lines = [_line_dict(l, names) for l in st.lines]
    q = [l for l in lines if l["state"] == "queue"]

    groups: dict[str, list[str]] = {}
    for l in q:
        groups.setdefault(l["counterparty_key"] or l["id"], []).append(l["id"])
    grouped = [{"key": k, "line_ids": ids,
                "name": next(x["counterparty_name"] for x in q if x["id"] in ids),
                "suggested": next(x["suggestion"] for x in q if x["id"] in ids)}
               for k, ids in groups.items() if len(ids) > 1]

    ready = [l for l in lines if l["state"] in ("pre_approved", "human_reviewed")]
    return {"statement": {"id": st.id, "label": st.label, "bank": st.bank,
                          "balance_ok": st.balance_ok,
                          "opening": _f(st.opening_balance),
                          "closing": _f(st.closing_balance),
                          "threshold": st.threshold, "calibrated": st.calibrated,
                          "posted": st.posted, "batch_id": st.batch_id,
                          "undone": undone_batches(s, sid)},
            "client": {"id": c.id, "name": c.name, "tag": c.tag},
            "ledgers": [{"guid": g, "name": n} for g, n in names.items()],
            "lines": lines, "groups": grouped,
            "materiality": _f(st.materiality),
            "ready": {"count": len(ready),
                      "sum": round(sum(l["amount"] for l in ready), 2)}}


def voucher_payload(s, sid: str) -> tuple[Statement, list[dict], str]:
    """(statement, [{date,narration,amount,direction,ledger_name}], bank_ledger).
    Raises KeyError naming the lines whose ledger is not in the synced masters."""
    st = s.get(Statement, sid)
    names = ledger_names(s, st.client_id)
    orphan = [l.id for l in st.lines if l.final_ledger_guid not in names]
    if orphan:
        raise KeyError(orphan)
    c = s.get(Client, st.client_id)
    return st, [{"date": l.date, "narration": l.narration_raw, "amount": _f(l.amount),
                 "direction": l.direction,
                 "ledger_name": names[l.final_ledger_guid]} for l in st.lines], c.bank_ledger


# ── review + learning ────────────────────────────────────────────────

def apply_review(s, sid: str, line_ids: list[str], action: str,
                 guid: str | None, actor: str) -> tuple[list[dict], list[dict]]:
    """Review one or many lines atomically. Returns (updated lines, proposals).

    The correction feeds the client's memory and a calibration point; it may
    PROPOSE a rule and never rewrites one — that stability rule lives in
    engine.memory.ClientMemory.learn and is called here, not reimplemented.
    """
    st = s.get(Statement, sid)
    cid = st.client_id
    names = ledger_names(s, cid)
    suspense = next((g for g, n in names.items() if "Suspense" in n), None)
    mem = load_memory(s, cid)

    updated, proposals = [], []
    for lid in line_ids:
        l = s.get(TransactionLine, lid)
        if l is None or l.statement_id != sid:
            continue
        sug = l.suggestion or {}
        if action == "accept":
            final = sug.get("ledger_guid") or suspense
        elif action == "reassign" and guid:
            final = guid
        else:
            final = suspense
        l.final_ledger_guid = final
        l.review_state = "human_reviewed"
        l.reviewed_by = actor
        l.reviewed_at = _now()

        s.add(CalibrationPoint(client_id=cid, raw_score=sug.get("score", 0.0),
                               was_correct=(action == "accept"), tier=sug.get("tier", "")))
        if final:
            p = _bump_pattern(s, cid, l.counterparty_key, final)
            if l.narration_norm:
                s.add(LabeledLine(client_id=cid, narration_norm=l.narration_norm,
                                  ledger_guid=final))
            proposal = mem.learn(l.counterparty_key, l.narration_norm, final)
            if proposal:
                proposals.append(proposal)
        audit(s, action=action, client_id=cid, statement_id=sid, line_id=lid,
              actor=actor, detail={"ledger_guid": final})
        updated.append(l)
    s.flush()
    return [_line_dict(l, names) for l in updated], proposals


def _bump_pattern(s, cid: str, key: str, guid: str):
    if not key:
        return None
    row = s.scalar(select(MemoryPattern).where(
        MemoryPattern.client_id == cid, MemoryPattern.counterparty_key == key,
        MemoryPattern.ledger_guid == guid))
    if row is None:
        row = MemoryPattern(client_id=cid, counterparty_key=key,
                            ledger_guid=guid, count=0)
        s.add(row)
    row.count += 1
    return row


# ── posting ──────────────────────────────────────────────────────────

def mark_posted(s, sid: str, n: int) -> str:
    """Mint the one batch id this statement will ever have. Idempotent by
    construction — callers check `posted` first and never reach here twice."""
    st = s.get(Statement, sid)
    st.batch_id = uuid.uuid4().hex[:6]
    st.posted = True
    s.add(VoucherBatch(statement_id=sid, batch_id=st.batch_id,
                       state="posted", vouchers=n))
    audit(s, action="post", client_id=st.client_id, statement_id=sid,
          actor="priya", detail={"batch": st.batch_id, "vouchers": n})
    return st.batch_id


def mark_undone(s, sid: str) -> str:
    st = s.get(Statement, sid)
    batch = st.batch_id
    row = s.scalar(select(VoucherBatch).where(VoucherBatch.batch_id == batch))
    if row:
        row.state, row.undone_at = "undone", _now()
    st.posted, st.batch_id = False, None
    audit(s, action="undo", client_id=st.client_id, statement_id=sid,
          actor="priya", detail={"batch": batch})
    return batch


def undone_batches(s, sid: str) -> list[str]:
    return [b.batch_id for b in s.scalars(
        select(VoucherBatch).where(VoucherBatch.statement_id == sid,
                                   VoucherBatch.state == "undone")
        .order_by(VoucherBatch.id))]


# ── audit: append only ───────────────────────────────────────────────
# There is no update_audit and no delete_audit, and there will not be one.

def audit(s, *, actor: str, action: str, client_id=None, statement_id=None,
          line_id=None, detail=None):
    s.add(AuditEvent(firm_id=FIRM_ID, client_id=client_id, statement_id=statement_id,
                     line_id=line_id, actor=actor, action=action, detail=detail or {}))


def audit_trail(s, statement_id: str) -> list[dict]:
    rows = s.scalars(select(AuditEvent).where(AuditEvent.statement_id == statement_id)
                     .order_by(AuditEvent.id)).all()
    return [{"id": r.id, "actor": r.actor, "action": r.action, "line": r.line_id,
             "detail": r.detail, "at": r.created_at.isoformat()} for r in rows]


# ── demo seed ────────────────────────────────────────────────────────

def is_empty() -> bool:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(Client)) == 0


def seed_demo(materiality: float):
    """The same two synthetic clients the dict store seeded, now persisted."""
    from engine.pipeline import classify_statement
    from harness.run import rows_to_stmt
    from harness.synth import gen_month, history_vouchers, make_client

    with session_scope() as s:
        s.add(Firm(id=FIRM_ID, name="Mehta & Associates",
                   materiality=Decimal(str(materiality))))

        warm = make_client()
        s.add(Client(id="sharma", firm_id=FIRM_ID, name="Sharma Textiles Pvt Ltd",
                     business=warm.business, bank_ledger=warm.bank_ledger,
                     tag="6 months history"))
        cold_c = make_client(seed=42)
        s.add(Client(id="deshmukh", firm_id=FIRM_ID, name="Deshmukh Auto Spares",
                     business="auto spare parts trading, Pune",
                     bank_ledger=cold_c.bank_ledger, tag="new client · cold start"))
        for cid, sc in (("sharma", warm), ("deshmukh", cold_c)):
            for l in sc.ledgers:
                s.add(LedgerMaster(client_id=cid, guid=l["guid"], name=l["name"],
                                   parent=l.get("parent", "")))
        s.flush()

        # warm client: bootstrap six months of Tally history into memory
        hist = history_vouchers(warm, [f"2026-{m:02d}" for m in range(2, 8)])
        counts: dict[tuple[str, str], int] = {}
        for v in hist:
            if v.get("counterparty_key"):
                k = (v["counterparty_key"], v["ledger_guid"])
                counts[k] = counts.get(k, 0) + 1
            if v.get("narration_norm"):
                s.add(LabeledLine(client_id="sharma", narration_norm=v["narration_norm"],
                                  ledger_guid=v["ledger_guid"]))
        for (key, guid), n in counts.items():
            s.add(MemoryPattern(client_id="sharma", counterparty_key=key,
                                ledger_guid=guid, count=n))
        s.flush()

        mem = load_memory(s, "sharma")
        ledgers = ledgers_of(s, "sharma")
        for k, mth in enumerate(["2026-05", "2026-06", "2026-07", "2026-04", "2026-03"]):
            rows, truth = gen_month(warm, mth, seed=555 + k)
            res = classify_statement(rows_to_stmt(rows), mem, ledgers,
                                     business=warm.business, materiality=materiality)
            for l, t in zip(res.lines, truth):
                if l.suggestion.ledger_guid:
                    s.add(CalibrationPoint(client_id="sharma",
                                           raw_score=l.suggestion.raw_score,
                                           was_correct=l.suggestion.ledger_guid == t,
                                           tier=l.suggestion.tier))
        s.flush()

        rows, _ = gen_month(warm, "2026-08", seed=999)
        res = classify_statement(rows_to_stmt(rows), mem, ledgers,
                                 business=warm.business,
                                 calibration=calibration_of(s, "sharma"),
                                 materiality=materiality)
        store_statement(s, "sharma", res, "HDFC ···5521 · Aug 2026", materiality)

        rows2, _ = gen_month(cold_c, "2026-08", seed=77)
        res2 = classify_statement(rows_to_stmt(rows2), ClientMemory("deshmukh"),
                                  ledgers_of(s, "deshmukh"),
                                  business="auto spare parts trading, Pune",
                                  materiality=materiality)
        store_statement(s, "deshmukh", res2, "Axis ···3318 · Aug 2026", materiality)


def wipe():
    """Tests only. Drops every row, keeps the schema."""
    with session_scope() as s:
        for model in (AuditEvent, VoucherBatch, CalibrationPoint, LabeledLine,
                      MemoryPattern, Rule, TransactionLine, Statement,
                      LedgerMaster, Client, Firm):
            s.execute(delete(model))
