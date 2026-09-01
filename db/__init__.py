"""Persistence. TRD §4's data model, and the session that serves it.

Two backends on purpose: Postgres for real (docker-compose.yml, port 5433) and
SQLite for tests. The suite runs on both, so a shape that only works on one is
a failing test rather than a surprise at the partner firm.
"""
