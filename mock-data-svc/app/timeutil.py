from __future__ import annotations

import re
from datetime import datetime, timezone

_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def clock_to_epoch(clock: str, base_date: str) -> float:
    m = _CLOCK_RE.match(clock.strip())
    if not m:
        raise ValueError(f"Geçersiz saat: {clock!r} (örn. 14:10)")
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if hh > 23 or mm > 59 or ss > 59:
        raise ValueError(f"Geçersiz saat: {clock!r}")
    day = datetime.fromisoformat(base_date).replace(tzinfo=timezone.utc)
    return day.timestamp() + hh * 3600 + mm * 60 + ss


def parse_ts(value: str, base_date: str) -> float:
    """'HH:MM' (base_date gününe oturtulur) veya ISO-8601 (Z destekli) -> epoch saniye."""
    text = value.strip()
    if _CLOCK_RE.match(text):
        return clock_to_epoch(text, base_date)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
