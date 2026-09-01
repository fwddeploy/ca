"""M0 test suite: parsing + balance check, enrichment, memory semantics,
calibration routing, Tally XML shape, end-to-end pipeline."""
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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
