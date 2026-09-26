"""Demo senaryosu: sentetik araç izleri.

Zaman çapası `anchor` (= şimdi). İzler τ = -7200 sn ... -30 sn aralığında 15 sn adımla üretilir;
τ=0 anı (şimdi) izde YOKTUR — o nokta pipeline'da DRN-03 kareleri georeferanslanınca eklenir.

Senaryo (üs: BASE_LAT/BASE_LON, kurgusal):
  V-101 pickup, V-102 truck, V-103 pickup : KAFİLE. Üssün 135° (GD) yönünde 7 km'de park halinde bekliyor,
      τ≈-467 sn'de harekete geçip 9 m/s ile DOĞRUDAN üsse yaklaşıyor. τ=0'da öncü araç 2800 m'de,
      araç aralığı 45 m. (CONVOY + DIRECT_APPROACH)
  V-201 pickup : 300° yönünde 3.6 km'de 2 saattir <100 m yarıçapta dolanıyor. (LOITERING)
  V-301 car    : 4.5-12 km bandında (seed=7 için gözlenen ~6-9 km) rastgele dolaşan sivil araç. (RANDOM)

DRN-03 geometrisiyle (mock-data-svc) ve detection-svc DEMO_SCENES ile birebir uyumludur; birini değiştirirseniz
diğerlerini de güncelleyin (README "Demo senaryosu").
"""
from __future__ import annotations

import math
import random
import time

from .config import Settings
from .geo import destination, from_enu, to_enu
from .store import TrackStore, Vehicle

STEP_S = 15
HISTORY_S = 7200
LAST_SEEDED_TAU = -30

CONVOY_BEARING = 135.0  # üsten kafileye doğru yön
CONVOY_STAGING_M = 7000.0
CONVOY_LEAD_NOW_M = 2800.0
CONVOY_SPEED_MPS = 9.0
CONVOY_DEPART_TAU = -(CONVOY_STAGING_M - CONVOY_LEAD_NOW_M) / CONVOY_SPEED_MPS  # ≈ -467 sn

# (id, sınıf, öncüden geri mesafe m, yanal sapma m)
CONVOY = [
    ("V-101", "pickup", 0.0, 1.5),
    ("V-102", "truck", 45.0, -1.0),
    ("V-103", "pickup", 90.0, 0.5),
]


def _taus() -> range:
    return range(-HISTORY_S, LAST_SEEDED_TAU + 1, STEP_S)


def _lead_distance(tau: float) -> float:
    if tau <= CONVOY_DEPART_TAU:
        return CONVOY_STAGING_M
    return CONVOY_LEAD_NOW_M + CONVOY_SPEED_MPS * (-tau)


def _noisy(lat: float, lon: float, sigma_m: float, rng: random.Random) -> tuple[float, float]:
    return from_enu(lat, lon, rng.gauss(0, sigma_m), rng.gauss(0, sigma_m))


def _convoy(anchor: float, cfg: Settings, rng: random.Random) -> list[Vehicle]:
    out: list[Vehicle] = []
    for vid, cls, behind, lateral in CONVOY:
        pts = []
        for tau in _taus():
            moving = tau > CONVOY_DEPART_TAU
            d = _lead_distance(tau) + behind
            lat, lon = destination(cfg.base_lat, cfg.base_lon, CONVOY_BEARING, d)
            wander = lateral + (1.2 * math.sin(tau / 40.0 + behind) if moving else 0.0)
            lat, lon = destination(lat, lon, CONVOY_BEARING + 90.0, wander)
            lat, lon = _noisy(lat, lon, 2.0 if moving else 1.5, rng)
            pts.append((anchor + tau, lat, lon))
        out.append(Vehicle(id=vid, cls=cls, points=pts, demo=True))
    return out


def _loiterer(anchor: float, cfg: Settings, rng: random.Random) -> Vehicle:
    clat, clon = destination(cfg.base_lat, cfg.base_lon, 300.0, 3600.0)
    e = n = 0.0
    pts = []
    for tau in _taus():
        e = 0.985 * e + rng.gauss(0, 5.0)  # Ornstein-Uhlenbeck: merkez etrafında sınırlı dolaşım
        n = 0.985 * n + rng.gauss(0, 5.0)
        if rng.random() < 0.08:
            continue  # ara sıra kayıp örnek
        lat, lon = from_enu(clat, clon, e, n)
        pts.append((anchor + tau, lat, lon))
    return Vehicle(id="V-201", cls="pickup", points=pts, demo=True)


def _random_car(anchor: float, cfg: Settings, rng: random.Random) -> Vehicle:
    start_lat, start_lon = destination(cfg.base_lat, cfg.base_lon, 200.0, 6500.0)
    e, n = to_enu(cfg.base_lat, cfg.base_lon, start_lat, start_lon)
    heading = rng.uniform(0, 360)
    pts = []
    for tau in _taus():
        r = math.hypot(e, n)
        if r > 12000:  # bandın dışına çıkarsa üsse doğru kıvır
            target = math.degrees(math.atan2(-e, -n)) % 360
            heading += 0.3 * (((target - heading + 180) % 360) - 180)
        elif r < 4500:  # üsse fazla yaklaşırsa uzaklaş
            target = math.degrees(math.atan2(e, n)) % 360
            heading += 0.6 * (((target - heading + 180) % 360) - 180)
        else:
            heading += rng.gauss(0, 35)
        speed = 0.0 if rng.random() < 0.15 else max(0.0, min(12.0, rng.gauss(6, 3)))
        e += speed * STEP_S * math.sin(math.radians(heading))
        n += speed * STEP_S * math.cos(math.radians(heading))
        lat, lon = from_enu(cfg.base_lat, cfg.base_lon, e, n)
        pts.append((anchor + tau, *_noisy(lat, lon, 3.0, rng)))
    return Vehicle(id="V-301", cls="car", points=pts, demo=True)


def seed_demo(store: TrackStore, cfg: Settings, now: float | None = None, rng_seed: int = 7) -> float:
    """Depoyu sıfırlayıp demo izlerini `now` çapasıyla yükler. Çapayı döndürür."""
    anchor = time.time() if now is None else now
    rng = random.Random(rng_seed)
    store.reset()
    for v in _convoy(anchor, cfg, rng):
        store.put_vehicle(v)
    store.put_vehicle(_loiterer(anchor, cfg, rng))
    store.put_vehicle(_random_car(anchor, cfg, rng))
    return anchor


def convoy_now_position(cfg: Settings) -> dict[str, tuple[float, float]]:
    """τ=0'daki gerçek kafile konumları (test/doğrulama için)."""
    out = {}
    for vid, _cls, behind, lateral in CONVOY:
        lat, lon = destination(cfg.base_lat, cfg.base_lon, CONVOY_BEARING, CONVOY_LEAD_NOW_M + behind)
        out[vid] = destination(lat, lon, CONVOY_BEARING + 90.0, lateral)
    return out

