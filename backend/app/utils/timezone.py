"""
Centralised timezone helpers for the GLR Attendance backend.

The EC2 server runs in UTC.  All business logic (check-in times, today's date,
checkin-status thresholds, etc.) must use IST (UTC+5:30).

Usage
-----
    from app.utils.timezone import now_ist, today_ist, IST

    now  = now_ist()          # timezone-aware datetime in IST
    today = today_ist()       # date object for the current IST day
"""

from datetime import datetime, date, timezone, timedelta

# Indian Standard Time: UTC + 5:30
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Return the current moment as a naive datetime containing the IST time.
    This prevents SQLAlchemy/psycopg2 from converting it back to UTC during DB insert."""
    return datetime.now(tz=IST).replace(tzinfo=None)


def today_ist() -> date:
    """Return the current calendar date in IST (may differ from UTC date)."""
    return now_ist().date()


def to_ist(dt: datetime) -> datetime:
    """Convert any datetime (naive UTC or aware) to IST."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Treat naive datetimes as UTC (how they are stored in the DB)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)
