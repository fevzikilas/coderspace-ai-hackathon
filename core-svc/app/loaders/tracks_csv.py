"""tracks.csv okuyucu — KATI (SADECE parse + doğrulama).

Başlık BİREBİR `track_id,time,lat,lon`; her satır 4 hücre; time = "HH:MM" (tarihsiz; `dataset_date` gününe oturtulur).
Reddedilenler: farklı/eksik başlık, hatalı hücre sayısı, boş satır, geçersiz saat/sayı/koordinat, aynı (track_id, time)
tekrarı, bir izde zamanın geriye gitmesi (yalnızca >12 saatlik düşüş gece yarısı taşması sayılır → gün +1).
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..timeutil import clock_to_epoch
from .strict import DatasetError

Point = tuple[float, float, float]  # (ts_epoch, lat, lon)
HEADER = ["track_id", "time", "lat", "lon"]
_TID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_NUM = re.compile(r"^-?\d{1,3}(\.\d+)?$")
_MAX_SHOWN = 8


def parse_tracks(text: str, base_date: str, source: str = "tracks.csv") -> dict[str, list[Point]]:
    rows = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(rows)
    except StopIteration:
        raise DatasetError(f"{source}: dosya boş") from None
    if header != HEADER:
        raise DatasetError(f"{source}: başlık {HEADER} olmalı, bulunan: {header!r}")

    errors: list[str] = []
    tracks: dict[str, list[Point]] = {}
    seen: set[tuple[str, str]] = set()
    offset: dict[str, float] = {}
    last_ts: dict[str, float] = {}

    def bad(line: int, msg: str) -> None:
        errors.append(f"  - satır {line}: {msg}")

    for line_no, row in enumerate(rows, start=2):
        if len(row) != 4:
            bad(line_no, f"4 hücre bekleniyor, {len(row)} bulundu: {row!r}")
            continue
        tid, clock, lat_s, lon_s = row
        if not _TID.match(tid):
            bad(line_no, f"geçersiz track_id {tid!r}")
            continue
        if not _CLOCK.match(clock):
            bad(line_no, f"time 'HH:MM' olmalı, bulunan {clock!r}")
            continue
        if not (_NUM.match(lat_s) and _NUM.match(lon_s)):
            bad(line_no, f"lat/lon sayı olmalı: {lat_s!r}, {lon_s!r}")
            continue
        lat, lon = float(lat_s), float(lon_s)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            bad(line_no, f"koordinat aralık dışı ({lat}, {lon})")
            continue
        if (tid, clock) in seen:
            bad(line_no, f"{tid} için {clock} tekrarlanıyor")
            continue
        seen.add((tid, clock))
        raw_ts = clock_to_epoch(clock, base_date)
        off = offset.get(tid, 0.0)
        if tid in last_ts:
            if raw_ts + off < last_ts[tid] - 12 * 3600:
                off += 86400.0  # gece yarısı taşması (ör. 23:55 -> 00:00)
            elif raw_ts + off < last_ts[tid]:
                bad(line_no, f"{tid}: zaman geriye gidiyor ({clock}); satırlar iz içinde artan sırada olmalı")
                continue
        offset[tid] = off
        ts = raw_ts + off
        last_ts[tid] = ts
        tracks.setdefault(tid, []).append((ts, lat, lon))

    if errors:
        more = f"\n  … ve {len(errors) - _MAX_SHOWN} hata daha" if len(errors) > _MAX_SHOWN else ""
        raise DatasetError(f"{source}: {len(errors)} hatalı satır\n" + "\n".join(errors[:_MAX_SHOWN]) + more)
    if not tracks:
        raise DatasetError(f"{source}: en az bir iz noktası gerekli")
    return tracks


def load_tracks(path: Path, base_date: str) -> dict[str, list[Point]]:
    return parse_tracks(path.read_text(encoding="utf-8"), base_date, path.name)
