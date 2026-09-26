from __future__ import annotations

import re
from datetime import datetime, timezone

_WINDOW_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def parse_window(value: str) -> float:
    """'2h' | '30m' | '90s' | '7200' -> saniye."""
    m = _WINDOW_RE.match(value)
    if not m:
        raise ValueError(f"Geçersiz pencere: {value!r} (örn. 2h, 30m, 90s)")
    return float(m.group(1)) * _UNIT[m.group(2).lower()]


def clock_to_epoch(clock: str, base_date: str) -> float:
    """'14:10' | '14:10:30' -> `base_date` (YYYY-MM-DD, UTC) günü üzerinde epoch saniye."""
    m = _CLOCK_RE.match(clock.strip())
    if not m:
        raise ValueError(f"Geçersiz saat: {clock!r} (örn. 14:10)")
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if hh > 23 or mm > 59 or ss > 59:
        raise ValueError(f"Geçersiz saat: {clock!r}")
    day = datetime.fromisoformat(base_date).replace(tzinfo=timezone.utc)
    return day.timestamp() + hh * 3600 + mm * 60 + ss


def parse_ts(value: str | float | int | None, default: float | None = None, base_date: str | None = None) -> float:
    """ISO-8601 (Z destekli), epoch saniye veya (base_date verilmişse) 'HH:MM' -> epoch saniye."""
    if value is None:
        if default is None:
            raise ValueError("zaman damgası yok")
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    if base_date is not None and _CLOCK_RE.match(text):
        return clock_to_epoch(text, base_date)
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
