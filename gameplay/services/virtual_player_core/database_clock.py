from __future__ import annotations

from datetime import UTC, datetime

from django.db import DatabaseError, connection


def database_utc_sql_expression() -> str:
    """Return a backend-safe SQL expression that evaluates to UTC now."""

    return "UTC_TIMESTAMP(6)" if connection.vendor == "mysql" else "CURRENT_TIMESTAMP"


def normalize_database_utc(value: object) -> datetime:
    """Normalize a value returned by a database-clock expression."""

    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise DatabaseError("database clock returned a non-datetime value")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def database_utc_now() -> datetime:
    """Read the database clock as an aware UTC datetime."""

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {database_utc_sql_expression()}")
        value = cursor.fetchone()[0]
    return normalize_database_utc(value)


__all__ = ["database_utc_now", "database_utc_sql_expression", "normalize_database_utc"]
