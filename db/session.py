"""Engine and session factory.

DATABASE_URL picks the backend. Default is a local SQLite file so a fresh
clone runs with no Docker; Postgres is docker-compose.yml on port 5433.

  set DATABASE_URL=postgresql+psycopg://ledger:ledger@localhost:5433/ledger_pilot

Nothing in the app reads DATABASE_URL directly — `configure()` exists so tests
can point the same code at either backend inside one process.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DEFAULT_URL = f"sqlite+pysqlite:///{Path(__file__).resolve().parents[1] / 'ledger_pilot.db'}"

_engine = None
_Session: sessionmaker | None = None


def url() -> str:
    return os.environ.get("DATABASE_URL") or DEFAULT_URL


def configure(database_url: str | None = None, echo: bool = False, create: bool = True):
    """(Re)point the process at a database. Returns the engine."""
    global _engine, _Session
    u = database_url or url()
    kwargs: dict = {"echo": echo, "future": True}
    if u.startswith("sqlite"):
        # one shared in-memory database across connections, when asked for one
        if ":memory:" in u:
            from sqlalchemy.pool import StaticPool
            kwargs |= {"poolclass": StaticPool,
                       "connect_args": {"check_same_thread": False}}
        else:
            kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(u, **kwargs)
    if u.startswith("sqlite"):
        # SQLite ignores foreign keys unless told not to; without this the
        # schema's referential guarantees exist on Postgres only.
        @event.listens_for(_engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    if create:
        Base.metadata.create_all(_engine)
    return _engine


def engine():
    if _engine is None:
        configure()
    return _engine


@contextmanager
def session_scope() -> Session:
    """A transaction. Commits on success, rolls back on anything raised —
    a half-applied review is worse than a rejected one."""
    if _Session is None:
        configure()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def reset():
    """Drop and recreate every table. Tests only."""
    e = engine()
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
