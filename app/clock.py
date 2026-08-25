"""Injectable clock.

Time is a financial input (authorization expiry). We inject it so tests can be
fully deterministic instead of racing the wall clock.
"""

from __future__ import annotations

from datetime import UTC, datetime


class Clock:
    """Wall-clock time source returning timezone-aware UTC datetimes."""

    def now(self) -> datetime:
        return datetime.now(UTC)


default_clock = Clock()


def get_clock() -> Clock:
    """FastAPI dependency; overridden in tests with a frozen clock."""
    return default_clock


def ensure_aware_utc(dt: datetime) -> datetime:
    """Normalise any datetime to timezone-aware UTC.

    SQLite does not preserve tzinfo, so values read back from the DB may be
    naive. We treat naive datetimes as UTC to keep comparisons correct.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
