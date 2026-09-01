"""End-to-end statement pipeline: parse → enrich → classify → route.
This is the function the API server wraps in M1 and the harness replays in M0."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import calibrate
from .enrich import enrich
from .llm import get_adapter
from .memory import ClientMemory
from .parsers import ParsedStatement, parse_file
from .tiers import SemanticIndex, Suggestion, annotate_ledgers, classify_line


@dataclass
class ClassifiedLine:
    date: str
    narration: str
    amount: float
    direction: str
    channel: str
    counterparty_key: str
    counterparty_name: str
    suggestion: Suggestion
    state: str            # auto_approved | queue
    normalized: str = ""


@dataclass
class Result:
    statement: ParsedStatement
    lines: list = field(default_factory=list)
    threshold: object = None

    @property
    def auto_rate(self) -> float:
        return (sum(1 for l in self.lines if l.state == "auto_approved")
                / max(len(self.lines), 1))


def classify_statement(source, memory: ClientMemory, ledgers: list[dict],
                       business: str = "", calibration=None, llm=None,
                       materiality: float = calibrate.MATERIALITY_DEFAULT) -> Result:
    """source: a file path or an already-ParsedStatement."""
    stmt = source if isinstance(source, ParsedStatement) else parse_file(source)
    ledgers = annotate_ledgers([dict(l) for l in ledgers])
    index = SemanticIndex(memory, ledgers)
    llm = llm or get_adapter()
    threshold = calibrate.fit_threshold(calibration or [])
    context = {"business": business, "ledgers": ledgers,
               "examples": memory.labeled[-50:]}
    ledger_names = {l["guid"]: l["name"] for l in ledgers}

    result = Result(stmt, threshold=threshold)
    for pl in stmt.lines:
        e = enrich(pl.narration)
        line = {"narration": pl.narration, "normalized": e.normalized,
                "direction": pl.direction, "amount": pl.amount, "channel": e.channel,
                "counterparty_key": e.counterparty_key,
                "counterparty_name": e.counterparty_name}
        sug = classify_line(line, memory, index, llm, context)
        first_seen = bool(e.counterparty_key) and e.counterparty_key not in memory.patterns
        state = calibrate.route(sug, pl.amount, threshold,
                                materiality=materiality,
                                first_seen_counterparty=first_seen)
        if sug.ledger_guid and sug.ledger_guid in ledger_names:
            sug.evidence.append(f"→ {ledger_names[sug.ledger_guid]}")
        result.lines.append(ClassifiedLine(
            pl.date, pl.narration, pl.amount, pl.direction, e.channel,
            e.counterparty_key, e.counterparty_name, sug, state, e.normalized))
    return result
