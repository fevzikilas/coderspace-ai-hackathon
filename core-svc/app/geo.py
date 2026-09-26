"""Küçük ölçekli coğrafi yardımcılar (küre modeli; birkaç on km için yeterli doğruluk)."""
from __future__ import annotations

import math

R_EARTH = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """(lat1,lon1) -> (lat2,lon2) başlangıç kerterizi, derece [0,360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    y = math.sin(dlmb) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return math.degrees(math.atan2(y, x)) % 360.0


def destination(lat: float, lon: float, bearing: float, dist_m: float) -> tuple[float, float]:
    b = math.radians(bearing)
    d = dist_m / R_EARTH
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def to_enu(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """(lat0,lon0) merkezli yerel düzlem: (doğu_m, kuzey_m). Equirectangular."""
    east = math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0))
    north = math.radians(lat - lat0) * R_EARTH
    return east, north


def from_enu(lat0: float, lon0: float, east: float, north: float) -> tuple[float, float]:
    lat = lat0 + math.degrees(north / R_EARTH)
    lon = lon0 + math.degrees(east / (R_EARTH * math.cos(math.radians(lat0))))
    return lat, lon


def angle_diff(a: float, b: float) -> float:
    """İmzalı açı farkı a-b, [-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0
