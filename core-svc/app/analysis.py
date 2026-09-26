"""Hareket analizi: hız, yön, yaklaşma, ETA."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .config import Settings
from .geo import angle_diff, bearing_deg, haversine_m, to_enu
from .store import Point
from .timeutil import iso


@dataclass(frozen=True)
class Base:
    lat: float
    lon: float
    radius_m: float


def _fit_velocity(pts: Sequence[Point], ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """En küçük kareler doğrusu: konum(t) = a + v*t. Dönüş: (vx, vy) m/s (doğu, kuzey)."""
    t0 = pts[-1][0]
    ts = [p[0] - t0 for p in pts]
    xs, ys = zip(*(to_enu(ref_lat, ref_lon, p[1], p[2]) for p in pts))
    n = len(pts)
    mt = sum(ts) / n
    var = sum((t - mt) ** 2 for t in ts)
    if var < 1e-9:
        return 0.0, 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((t - mt) * (x - mx) for t, x in zip(ts, xs)) / var
    vy = sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / var
    return vx, vy


def median_step(points: Sequence[Point]) -> float:
    """İzdeki ardışık noktalar arası medyan süre (sn); tek nokta için 0."""
    diffs = sorted(b[0] - a[0] for a, b in zip(points, points[1:]) if b[0] > a[0])
    return diffs[len(diffs) // 2] if diffs else 0.0


def analyze_points(points: Sequence[Point], base: Base, cfg: Settings, reference_ts: float) -> dict[str, Any]:
    """Son birkaç dakikalık izden hareket özeti üretir.

    `reference_ts` = değerlendirme anı (olayda capture_time). Duvar saati KULLANILMAZ; `stale` bayrağı
    `reference_ts - son_nokta` farkıdır. Hız penceresi son NOKTAYA göre kesilir ve örnekleme aralığına uyarlanır:
    max(analysis_window_s, 3.5 x medyan adım) — 15 sn'lik izde ~2 dk, 5 dk'lık izde ~17 dk (4 nokta).
    """
    if not points:
        return _empty(base, "izde nokta yok")
    last = points[-1]
    dist = haversine_m(last[1], last[2], base.lat, base.lon)
    bearing_to_base = bearing_deg(last[1], last[2], base.lat, base.lon)

    window_s = max(cfg.analysis_window_s, 3.5 * median_step(points))
    window = [p for p in points if last[0] - p[0] <= window_s]
    if len(window) < 3 and len(points) >= 2:
        window = list(points[-3:]) if len(points) >= 3 else list(points)
    stale = (reference_ts - last[0]) > cfg.stale_after_s

    result: dict[str, Any] = {
        "speed_mps": 0.0,
        "heading_deg": None,
        "approaching": False,
        "eta_min": None,
        "distance_to_base_m": round(dist, 1),
        "bearing_to_base_deg": round(bearing_to_base, 1),
        "closing_speed_mps": 0.0,
        "heading_deviation_deg": None,
        "within_base": dist <= base.radius_m,
        "points_used": len(window),
        "window_s": round(window[-1][0] - window[0][0], 1) if len(window) > 1 else 0.0,
        "last_seen": iso(last[0]),
        "last_position": {"lat": last[1], "lon": last[2]},
        "stale": stale,
        "insufficient_data": len(window) < 2,
    }
    if len(window) < 2:
        return result

    vx, vy = _fit_velocity(window, last[1], last[2])
    speed = math.hypot(vx, vy)
    result["speed_mps"] = round(speed, 2)
    if speed >= cfg.stationary_mps:
        heading = math.degrees(math.atan2(vx, vy)) % 360.0
        result["heading_deg"] = round(heading, 1)
        result["heading_deviation_deg"] = round(abs(angle_diff(heading, bearing_to_base)), 1)

    # Üsse doğru radyal hız bileşeni (m/s). Üs yönü birim vektörü: last -> base
    bx, by = to_enu(last[1], last[2], base.lat, base.lon)
    norm = math.hypot(bx, by)
    closing = (vx * bx + vy * by) / norm if norm > 1e-6 else 0.0
    result["closing_speed_mps"] = round(closing, 2)
    approaching = closing >= cfg.approach_min_mps
    result["approaching"] = approaching
    if approaching:
        remaining = max(0.0, dist - base.radius_m)
        result["eta_min"] = round(remaining / closing / 60.0, 2)
    return result


def _empty(base: Base, reason: str) -> dict[str, Any]:
    return {
        "speed_mps": 0.0,
        "heading_deg": None,
        "approaching": False,
        "eta_min": None,
        "distance_to_base_m": None,
        "bearing_to_base_deg": None,
        "closing_speed_mps": 0.0,
        "heading_deviation_deg": None,
        "within_base": False,
        "points_used": 0,
        "window_s": 0.0,
        "last_seen": None,
        "last_position": None,
        "stale": True,
        "insufficient_data": True,
        "note": reason,
    }
