from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def parse_local_cutoff(value: str, tz_name: str) -> datetime:
    """Parse an ISO-like local datetime and return an aware UTC datetime."""
    text = value.strip()
    if text.endswith("Z"):
        dt = datetime.fromisoformat(text[:-1] + "+00:00")
    else:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc)


def to_millis(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(dt.timestamp() * 1000)


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
