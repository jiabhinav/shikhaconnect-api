"""Best-effort startup locks for managed PostgreSQL roles.

Some hosted PostgreSQL roles cannot execute advisory-lock functions. Keep
initialization usable for those roles while preserving serialization whenever
the function is permitted. A savepoint keeps the surrounding schema
transaction valid after PostgreSQL reports insufficient privilege.
"""
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def acquire_transaction_lock(connection, key):
    if connection.dialect.name not in ("postgresql", "postgres"):
        return

    try:
        with connection.begin_nested():
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    except DBAPIError as exc:
        original = exc.orig
        sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        if sqlstate != "42501":  # insufficient_privilege
            raise
