"""Piksel -> yer koordinatı (GPS) dönüşümü.

Model: düz zemin (z=0), iğne-deliği kamera. `alt` yerden yükseklik (AGL), `fov` YATAY görüş açısı (derece),
`sensor_w/sensor_h` görüntü boyutu (piksel). `gimbal_pitch`: 0 = ufuk, -90 = nadir.
Görüntü yukarısı = kamera yönü (heading, kuzeyden saat yönünde derece).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .geo import from_enu


@dataclass(frozen=True)
class DroneMeta:
    lat: float
    lon: float
    alt: float
    heading: float
    gimbal_pitch: float
    fov: float
    sensor_w: float
    sensor_h: float


def pixel_to_ground(u: float, v: float, meta: DroneMeta) -> tuple[float, float] | None:
    """(u,v) pikselini zemine izdüşürür. Işın zemine inmiyorsa (ufuk üstü) None döner."""
    w, h = meta.sensor_w, meta.sensor_h
    fx = (w / 2.0) / math.tan(math.radians(meta.fov) / 2.0)  # kare piksel varsayımı
    xn = (u - w / 2.0) / fx
    yn = (v - h / 2.0) / fx

    psi = math.radians(meta.heading)
    theta = math.radians(-abs(meta.gimbal_pitch))  # aşağı bakış: negatif

    # Dünya ekseni (doğu, kuzey, yukarı) içinde kamera tabanı
    fwd = (math.cos(theta) * math.sin(psi), math.cos(theta) * math.cos(psi), math.sin(theta))
    right = (math.cos(psi), -math.sin(psi), 0.0)
    down = (math.sin(theta) * math.sin(psi), math.sin(theta) * math.cos(psi), -math.cos(theta))

    rx = fwd[0] + xn * right[0] + yn * down[0]
    ry = fwd[1] + xn * right[1] + yn * down[1]
    rz = fwd[2] + xn * right[2] + yn * down[2]
    if rz >= -1e-6:
        return None
    scale = meta.alt / -rz
    return from_enu(meta.lat, meta.lon, rx * scale, ry * scale)


def box_anchor(x1: float, y1: float, x2: float, y2: float, gimbal_pitch: float) -> tuple[float, float]:
    """Nadire yakın görüntüde bbox merkezi, eğik görüntüde zemine temas eden alt-orta nokta."""
    cx = (x1 + x2) / 2.0
    if abs(gimbal_pitch) >= 80.0:
        return cx, (y1 + y2) / 2.0
    return cx, y2


# --------------------------------------------------------------------------------------------------
# Köşe koordinatlı görüntü: piksel -> WGS84 — RESMÎ FORMÜL (gorev_tanimi.pdf, "Konum dönüşümü"). Değiştirmeyin.
#   boylam = sol_üst.boylam + (x / genişlik_px)  * (sağ_üst.boylam - sol_üst.boylam)
#   enlem  = sol_üst.enlem  + (y / yükseklik_px) * (sol_alt.enlem  - sol_üst.enlem)
# Görüntü kuşbakışı/ortorektifiye kabul edilir (üst kenar kuzey, sol kenar batı). İki eksen AYRI ayrı doğrusal
# enterpolasyondur: boylam yalnızca üst kenardan, enlem yalnızca sol kenardan gelir. `sağ_alt` formülde YOKTUR
# (bu yüzden fonksiyon imzasında da yoktur). x, y: sol üst köşe (0, 0).
# --------------------------------------------------------------------------------------------------
LatLon = tuple[float, float]


def pixel_to_ground_corners(x: float, y: float, top_left: LatLon, top_right: LatLon, bottom_left: LatLon, width_px: float, height_px: float) -> LatLon:
    """(x, y) pikselinin (enlem, boylam)'ı."""
    lon = top_left[1] + (x / width_px) * (top_right[1] - top_left[1])
    lat = top_left[0] + (y / height_px) * (bottom_left[0] - top_left[0])
    return lat, lon


def bbox_center(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
    """Araç konumu = kutunun MERKEZİ (resmî kural): x = x1 + w/2, y = y1 + h/2."""
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0
