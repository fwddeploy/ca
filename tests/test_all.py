"""M0 test suite: parsing + balance check, enrichment, memory semantics,
calibration routing, Tally XML shape, end-to-end pipeline.

Plus the M1 posting-safety tests: exactly one batch id per statement, and reads
that do not write. Both of those were live defects — see the `_api` helpers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.tally_xml import masters_xml, undo_xml, voucher_xml
from engine import calibrate
from engine.enrich import enrich
from engine.memory import ClientMemory, Rule
from engine.parsers import ParsedLine, validate_balance
from engine.tiers import Suggestion


def test_enrich_upi():
    e = enrich("UPI/DR/523114832101/RAMESH TRADERS/YESB/ramesht.9@ybl")
    assert e.channel == "upi"
    assert e.refs["vpa"] == "ramesht.9@ybl"
    assert e.counterparty_key == "vpa:ramesht.9@ybl"
    assert "Ramesh Traders" in e.counterparty_name


def test_enrich_neft_and_charges():
    assert enrich("NEFT DR-GANGA DYEING-HDFCN12345678901").channel == "neft"
    assert enrich("CONSOLIDATED CHARGES FOR A/C + GST").channel == "bank_charges"
    assert enrich("UPI REV/523/FAILED TXN").channel == "reversal"


def test_balance_validation_catches_tamper():
    good = [ParsedLine("2026-08-01", "A", 100.0, "cr", 1100.0),
            ParsedLine("2026-08-02", "B", 50.0, "dr", 1050.0)]
    o, c, ok, br = validate_balance(good)
    assert ok and o == 1000.0 and c == 1050.0
    bad = [ParsedLine("2026-08-01", "A", 100.0, "cr", 1100.0),
           ParsedLine("2026-08-02", "B", 50.0, "dr", 1200.0)]  # tampered
    assert validate_balance(bad)[2] is False


def test_memory_abstains_on_split_history():
    m = ClientMemory("c1")
    for _ in range(3):
        m.learn("vpa:x@ybl", "x traders", "g-1")
    guid, cons, _ = m.match_pattern("vpa:x@ybl")
    assert guid == "g-1"
    m.learn("vpa:x@ybl", "x traders", "g-2")
    m.learn("vpa:x@ybl", "x traders", "g-2")
    guid, cons, hist = m.match_pattern("vpa:x@ybl")   # 3-2 split → abstain
    assert guid is None and len(hist) == 2


def test_rule_specificity_and_stability():
    m = ClientMemory("c1")
    m.rules = [Rule("swiggy", "partial", "g-welfare"),
               Rule("swiggy ltd corporate", "exact", "g-other")]
    assert m.match_rule("swiggy ltd corporate") == "g-other"   # exact beats partial
    # corrections never rewrite rules — learn() touches patterns only
    m.learn("vpa:swiggy@axl", "swiggy", "g-elsewhere")
    assert m.match_rule("something swiggy something") == "g-welfare"


def test_calibration_routing_guardrails():
    th = calibrate.fit_threshold([])            # no data → conservative default
    assert th.value == calibrate.CONSERVATIVE_DEFAULT and not th.calibrated
    s_high = Suggestion("g-1", None, "T1", 0.97)
    assert calibrate.route(s_high, 5000, th) == "auto_approved"
    # materiality: big amount queues even at high confidence
    assert calibrate.route(s_high, 400000, th) == "queue"
    # T0 rules are exempt from materiality
    s_rule = Suggestion("g-1", None, "T0", 1.0)
    assert calibrate.route(s_rule, 400000, th) == "auto_approved"
    # calibrated threshold obeys the (errors+1)/(n+1) bound
    cal = [(0.99, True)] * 300 + [(0.85, True)] * 100 + [(0.84, False)] * 50
    th2 = calibrate.fit_threshold(cal)
    assert th2.calibrated and th2.value > 0.84


def test_tally_xml_idempotent_refs():
    lines = [{"date": "2026-08-01", "narration": "UPI/DR/1/RAMESH & CO", "amount": 100.5,
              "direction": "dr", "ledger_name": "Purchases - Yarn"}]
    xml = voucher_xml(lines, "HDFC Bank A/c 5521", "b42")
    assert "LP-b42-0001" in xml and "&amp;" in xml and 'VCHTYPE="Payment"' in xml
    assert "<AMOUNT>-100.50</AMOUNT>" in xml
    assert "All Masters" in masters_xml([{"name": "New Ledger", "parent": "Sundry Creditors"}])
    assert 'ACTION="Cancel"' in undo_xml("b42", 1, {1: "2026-08-01"})


def test_end_to_end_smoke():
    from engine.pipeline import classify_statement
    from harness.run import rows_to_stmt
    from harness.synth import gen_month, make_client
    client = make_client()
    rows, truth = gen_month(client, "2026-08", seed=1)
    res = classify_statement(rows_to_stmt(rows), ClientMemory("c"), client.ledgers,
                             business=client.business)
    assert len(res.lines) == len(truth)
    assert res.statement.balance_ok
    assert all(l.state in ("auto_approved", "queue") for l in res.lines)


# ── M1 posting safety ────────────────────────────────────────────────
# Importing api.main seeds two demo clients, so these are kept together and the
# import is lazy: the M0 tests above must not pay for it.

_CLIENT, _SEED = None, [8000]


def _api():
    """(TestClient, fresh statement id) — a new fully-reviewed statement each
    call, so tests cannot leak posted state into one another."""
    from fastapi.testclient import TestClient

    from api.main import DB, MATERIALITY, _store_statement, app
    from engine.pipeline import classify_statement
    from harness.run import rows_to_stmt
    from harness.synth import gen_month, make_client

    _SEED[0] += 1
    c = DB["clients"]["sharma"]
    rows, _ = gen_month(make_client(), "2026-10", seed=_SEED[0])
    res = classify_statement(rows_to_stmt(rows), c["memory"], c["ledgers"],
                             business=c["business"], materiality=MATERIALITY)
    sid = _store_statement(c, res, f"posting-safety {_SEED[0]}")
    client = TestClient(app)
    for line in DB["statements"][sid]["lines"]:
        if line["state"] == "queue":
            client.post(f"/api/lines/{line['id']}/review", json={"action": "accept"})
    return client, sid, DB["statements"][sid]


def test_get_xml_never_posts():
    """GET /xml used to call post_statement(), so reading the XML marked the
    statement posted under a batch id the operator never asked for."""
    client, sid, s = _api()
    assert s["posted"] is False
    r = client.get(f"/api/statements/{sid}/xml")
    assert r.status_code == 409, "unposted statement must not hand out XML"
    assert s["posted"] is False, "a GET wrote"
    assert s["batch_id"] is None, "a GET minted a batch id"

    batch = client.post(f"/api/statements/{sid}/post").json()["batch_id"]
    r2 = client.get(f"/api/statements/{sid}/xml")
    assert r2.status_code == 200 and f"LP-{batch}-0001" in r2.text
    assert s["batch_id"] == batch, "a GET rewrote the batch id"


def test_posting_is_idempotent():
    """Two posts used to mint two batch ids, so the same bank line reached Tally
    under two LP- refs and undo (keyed on the batch) could only cancel one."""
    client, sid, s = _api()
    a = client.post(f"/api/statements/{sid}/post").json()
    b = client.post(f"/api/statements/{sid}/post").json()
    assert a["batch_id"] == b["batch_id"], "second post minted a new batch"
    assert a["xml"] == b["xml"]
    assert b["already_posted"] is True and a["already_posted"] is False
    assert s["batch_id"] == a["batch_id"]


def test_undo_releases_the_batch():
    client, sid, s = _api()
    batch = client.post(f"/api/statements/{sid}/post").json()["batch_id"]
    u = client.post(f"/api/statements/{sid}/undo").json()
    assert u["batch_id"] == batch and u["cancelled"] == len(s["lines"])
    assert 'ACTION="Cancel"' in u["xml"] and f"LP-{batch}-0001" in u["xml"]
    assert s["posted"] is False and batch in s["undone"]
    assert client.post(f"/api/statements/{sid}/undo").status_code == 409
    # a re-post after undo is a genuinely new batch — the old one was cancelled
    assert client.post(f"/api/statements/{sid}/post").json()["batch_id"] != batch


def test_unreviewed_lines_never_export():
    """PRD REV-6: nothing exports unreviewed."""
    from api.main import DB
    client, sid, s = _api()
    s["lines"][0]["state"] = "queue"
    assert client.post(f"/api/statements/{sid}/post").status_code == 409
    assert client.get(f"/api/statements/{sid}/xml").status_code == 409
    assert s["posted"] is False
    assert DB["statements"][sid]["batch_id"] is None


# ── M1 upload: parse preview, then classify ──────────────────────────


def _hdfc_csv(tamper_row: int | None = None) -> bytes:
    """An HDFC-layout statement as bytes. tamper_row edits one closing balance,
    the way an altered PDF would, so the continuity chain must break there."""
    from harness.synth import gen_month, make_client
    rows, _ = gen_month(make_client(), "2026-11", seed=4242)
    if tamper_row is not None:
        rows[tamper_row]["balance"] = round(rows[tamper_row]["balance"] + 75_000, 2)
    out = ["Date,Narration,Withdrawal,Deposit,Closing Balance"]
    for r in rows:
        y, m, d = r["date"].split("-")
        out.append(",".join([
            f"{d}/{m}/{y[2:]}", '"' + r["narration"].replace('"', "") + '"',
            f"{r['amount']}" if r["direction"] == "dr" else "",
            f"{r['amount']}" if r["direction"] == "cr" else "",
            f"{r['balance']}"]))
    return ("\n".join(out) + "\n").encode()


def _upload(client, cid, name, blob):
    return client.post(f"/api/clients/{cid}/upload",
                       files={"file": (name, blob, "text/csv")})


def test_upload_previews_but_does_not_classify():
    """PRD core loop: parse preview + balance check comes *before* any
    classification. The upload used to classify and store in one shot."""
    from fastapi.testclient import TestClient

    from api.main import DB, app
    client = TestClient(app)
    before = set(DB["statements"])

    r = _upload(client, "sharma", "nov.csv", _hdfc_csv())
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["bank"] == "HDFC" and p["lines"] > 0
    assert p["balance_ok"] is True and p["balance_breaks"] == []
    assert p["opening"] is not None and p["closing"] is not None
    assert len(p["sample"]) == 12
    assert set(DB["statements"]) == before, "preview created a statement"

    c = client.post(f"/api/clients/sharma/previews/{p['preview_id']}/classify")
    assert c.status_code == 200, c.text
    sid = c.json()["statement_id"]
    assert sid not in before and len(DB["statements"][sid]["lines"]) == p["lines"]
    assert client.get(f"/api/statements/{sid}/queue").status_code == 200


def test_broken_balance_chain_blocks_classification():
    """TRD §5: the continuity check blocks classification and names the rows."""
    from fastapi.testclient import TestClient

    from api.main import DB, app
    client = TestClient(app)
    before = set(DB["statements"])

    p = _upload(client, "sharma", "tampered.csv", _hdfc_csv(tamper_row=9)).json()
    assert p["balance_ok"] is False
    assert p["balance_breaks"], "tampered balance was not detected"
    assert p["balance_breaks"][0]["row"] == 10          # 0-indexed row 9

    r = client.post(f"/api/clients/sharma/previews/{p['preview_id']}/classify")
    assert r.status_code == 409 and "Balance continuity" in r.json()["detail"]
    assert set(DB["statements"]) == before, "a non-reconciling statement was classified"


def test_upload_rejects_what_it_cannot_read():
    from fastapi.testclient import TestClient

    from api.main import app
    client = TestClient(app)

    r = _upload(client, "sharma", "scan.pdf", b"%PDF-1.4 not really")
    assert r.status_code == 415 and "v1.5" in r.json()["detail"]

    r = _upload(client, "sharma", "notes.docx", b"nope")
    assert r.status_code == 415

    r = _upload(client, "sharma", "mystery.csv", b"Alpha,Beta,Gamma\n1,2,3\n")
    assert r.status_code == 422 and "No bank format matched" in r.json()["detail"]

    assert _upload(client, "nosuchclient", "x.csv", _hdfc_csv()).status_code == 404


def test_preview_is_scoped_to_its_client():
    from fastapi.testclient import TestClient

    from api.main import app
    client = TestClient(app)
    pid = _upload(client, "sharma", "nov.csv", _hdfc_csv()).json()["preview_id"]
    assert client.post(f"/api/clients/deshmukh/previews/{pid}/classify").status_code == 404
    assert client.post(f"/api/clients/sharma/previews/{pid}/classify").status_code == 200


# ── persistence (TRD §4) ─────────────────────────────────────────────

PG_URL = "postgresql+psycopg://ledger:ledger@localhost:5433/ledger_pilot"


def _backends():
    """SQLite always; Postgres too when docker-compose.yml is up. A schema that
    works on one backend and not the other is a defect we want to see here, not
    at the partner firm — but the suite must not require Docker to run."""
    import os

    urls = [("sqlite", "sqlite+pysqlite:///:memory:")]
    if os.environ.get("LP_SKIP_PG") != "1":
        try:
            import socket
            with socket.create_connection(("localhost", 5433), timeout=0.6):
                urls.append(("postgres", PG_URL))
        except OSError:
            pass
    return urls


def test_schema_round_trips_on_every_backend():
    from decimal import Decimal

    from sqlalchemy import inspect

    from db import session as dbs
    from db.models import AuditEvent, Client, Firm, Statement, TransactionLine

    checked = []
    for name, u in _backends():
        dbs.configure(u, create=False)
        dbs.reset()
        assert len(inspect(dbs.engine()).get_table_names()) == 13

        with dbs.session_scope() as s:
            s.add(Firm(id="f1", name="Mehta & Associates", materiality=200_000))
            s.add(Client(id="c1", firm_id="f1", name="Sharma Textiles"))
            s.add(Statement(id="s1", client_id="c1", opening_balance=482110.50,
                            balance_breaks=[4, 9]))
            s.add(TransactionLine(id="s1-0", statement_id="s1", seq=0,
                                  date="2026-08-01", narration_raw="UPI/DR/1/RAMESH & CO",
                                  amount=Decimal("12345.67"), direction="dr",
                                  refs={"vpa": "r@ybl"},
                                  suggestion={"tier": "T1", "score": 0.97}))
            s.add(AuditEvent(actor="priya", action="post", detail={"batch": "abc"}))

        with dbs.session_scope() as s:
            # money must survive as an exact decimal, not a float approximation
            assert s.get(TransactionLine, "s1-0").amount == Decimal("12345.67")
            assert s.get(Statement, "s1").opening_balance == Decimal("482110.50")
            assert s.get(Statement, "s1").balance_breaks == [4, 9]
            assert s.get(TransactionLine, "s1-0").refs == {"vpa": "r@ybl"}
            assert s.query(AuditEvent).one().detail == {"batch": "abc"}

        # referential integrity must hold on SQLite too (PRAGMA foreign_keys)
        try:
            with dbs.session_scope() as s:
                s.add(Statement(id="orphan", client_id="nope"))
            raise AssertionError(f"{name}: accepted a statement with no client")
        except Exception as exc:
            assert "Integrity" in type(exc).__name__, f"{name}: {exc!r}"
        checked.append(name)

    assert "sqlite" in checked
    print(f"      backends checked: {', '.join(checked)}", end="")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
