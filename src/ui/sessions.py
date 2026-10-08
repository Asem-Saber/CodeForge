"""The keyboard session picker and its rendering adapters."""
from datetime import datetime, timezone

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def _as_utc(value: datetime) -> datetime:
    """Treat a naive datetime as UTC so the subtraction below cannot raise."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def format_age(updated_at: datetime | None, now: datetime | None = None) -> str:
    """How long ago a session was last checkpointed, in words."""
    if updated_at is None:
        return "unknown"

    now = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    seconds = max(0, int((now - _as_utc(updated_at)).total_seconds()))

    if seconds < MINUTE:
        return "just now"
    if seconds < HOUR:
        return f"{seconds // MINUTE}m ago"
    if seconds < DAY:
        return f"{seconds // HOUR}h ago"
    if seconds < 2 * DAY:
        return "yesterday"
    return f"{seconds // DAY}d ago"
