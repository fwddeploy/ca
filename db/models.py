"""TRD §4 data model.

Rules this schema encodes, not just stores:

  * A ledger is referenced by its **Tally GUID**, never its name. Names are
    denormalised onto rows for display only. (docs/decisions.md — the GnuCash
    rename bug.)
  * `TransactionLine.review_state` is the single source of truth for whether a
    line is done, and `suggestion` keeps what the AI said even after a human
    overrode it — calibration trains on that delta.
  * `AuditEvent` is append-only. Nothing in `db/store.py` updates or deletes
    one. That is enforced in code, not by the database: a Postgres rule could
    do it, SQLite cannot, and a guarantee that holds on only one backend is
    not a guarantee.
  * Money is `Numeric(14, 2)`. The engine works in float and always has; the
    store converts at its own boundary so exact decimals live in the database
    without the engine changing shape.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        Numeric, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(14, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Firm(Base):
    __tablename__ = "firms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # firm-configurable guardrail: lines at or above this queue regardless of
    # confidence. See README — quoting an auto-rate without it means nothing.
    materiality: Mapped[float] = mapped_column(MONEY, default=50_000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    users: Mapped[list[User]] = relationship(back_populates="firm")
    clients: Mapped[list[Client]] = relationship(back_populates="firm")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    firm_id: Mapped[str] = mapped_column(ForeignKey("firms.id"), index=True)
    email: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))        # operator | approver | admin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    firm: Mapped[Firm] = relationship(back_populates="users")
    __table_args__ = (UniqueConstraint("firm_id", "email", name="uq_user_firm_email"),)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    firm_id: Mapped[str] = mapped_column(ForeignKey("firms.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    business: Mapped[str] = mapped_column(Text, default="")
    bank_ledger: Mapped[str] = mapped_column(String(200), default="")
    tag: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    firm: Mapped[Firm] = relationship(back_populates="clients")
    ledgers: Mapped[list[LedgerMaster]] = relationship(
        back_populates="client", cascade="all, delete-orphan")
    statements: Mapped[list[Statement]] = relationship(
        back_populates="client", cascade="all, delete-orphan")


class LedgerMaster(Base):
    """One row per ledger in the client's Tally chart, as last synced."""
    __tablename__ = "ledger_masters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    guid: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    parent: Mapped[str] = mapped_column(String(200), default="")
    gstin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gloss: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    client: Mapped[Client] = relationship(back_populates="ledgers")
    __table_args__ = (UniqueConstraint("client_id", "guid", name="uq_ledger_client_guid"),)


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    bank: Mapped[str] = mapped_column(String(60))
    account_tail: Mapped[str] = mapped_column(String(8), default="")
    ledger_guid: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Statement(Base):
    __tablename__ = "statements"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    bank_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("bank_accounts.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    bank: Mapped[str] = mapped_column(String(60), default="")
    filename: Mapped[str] = mapped_column(String(260), default="")
    opening_balance: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    closing_balance: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    balance_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    balance_breaks: Mapped[list] = mapped_column(JSON, default=list)
    threshold: Mapped[float] = mapped_column(Float, default=0.92)
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    materiality: Mapped[float] = mapped_column(MONEY, default=50_000)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    batch_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    client: Mapped[Client] = relationship(back_populates="statements")
    lines: Mapped[list[TransactionLine]] = relationship(
        back_populates="statement", cascade="all, delete-orphan",
        order_by="TransactionLine.seq")
    batches: Mapped[list[VoucherBatch]] = relationship(
        back_populates="statement", cascade="all, delete-orphan")


class TransactionLine(Base):
    __tablename__ = "transaction_lines"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)

    date: Mapped[str] = mapped_column(String(10))              # ISO yyyy-mm-dd
    narration_raw: Mapped[str] = mapped_column(Text)
    narration_norm: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[float] = mapped_column(MONEY)
    direction: Mapped[str] = mapped_column(String(2))          # dr | cr
    balance: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    channel: Mapped[str] = mapped_column(String(24), default="other")
    refs: Mapped[dict] = mapped_column(JSON, default=dict)     # utr/rrn/vpa/ifsc/gstin/cheque
    counterparty_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    counterparty_name: Mapped[str] = mapped_column(String(200), default="")

    # what the AI said — preserved even after a human overrides it, because
    # calibration trains on the delta between this and the final ledger.
    suggestion: Mapped[dict] = mapped_column(JSON, default=dict)

    # review.state is the single source of truth for "is this line done"
    review_state: Mapped[str] = mapped_column(String(20), default="queue", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    final_ledger_guid: Mapped[str | None] = mapped_column(String(80), nullable=True)

    statement: Mapped[Statement] = relationship(back_populates="lines")


class Rule(Base):
    """T0. Stable by contract: a correction may PROPOSE one, never rewrite one."""
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    pattern: Mapped[str] = mapped_column(String(240))
    match_type: Mapped[str] = mapped_column(String(10))        # exact|prefix|suffix|partial
    ledger_guid: Mapped[str] = mapped_column(String(80))
    scope: Mapped[str] = mapped_column(String(10), default="client")   # client | firm
    stable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_from: Mapped[str] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("client_id", "pattern", "match_type",
                                       name="uq_rule_client_pattern"),)


class MemoryPattern(Base):
    """T1. counterparty_key → {ledger_guid: count}, one row per pair."""
    __tablename__ = "memory_patterns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    counterparty_key: Mapped[str] = mapped_column(String(120), index=True)
    ledger_guid: Mapped[str] = mapped_column(String(80))
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("client_id", "counterparty_key", "ledger_guid",
                                       name="uq_pattern_client_key_ledger"),)


class LabeledLine(Base):
    """T2's corpus: every confirmed (normalized narration → ledger) for a client."""
    __tablename__ = "labeled_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    narration_norm: Mapped[str] = mapped_column(Text)
    ledger_guid: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CalibrationPoint(Base):
    """(raw_score, was_correct) from a reviewed line — feeds fit_threshold."""
    __tablename__ = "calibration_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    raw_score: Mapped[float] = mapped_column(Float)
    was_correct: Mapped[bool] = mapped_column(Boolean)
    tier: Mapped[str] = mapped_column(String(8), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VoucherBatch(Base):
    """One posting of one statement. `LP-{batch_id}-{seq}` is the idempotency
    key that reaches Tally, so batch_id is unique across the whole table."""
    __tablename__ = "voucher_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id"), index=True)
    batch_id: Mapped[str] = mapped_column(String(16), unique=True)
    state: Mapped[str] = mapped_column(String(12), default="posted")  # posted | undone
    vouchers: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    undone_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    statement: Mapped[Statement] = relationship(back_populates="batches")


class AuditEvent(Base):
    """Append-only. db/store.py offers no update or delete for this table."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    firm_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    statement_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    line_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
