import time

from app.analysis import Base, analyze_points
from app.config import Settings
from app.geo import destination
from app.store import TrackStore, Vehicle

CFG = Settings()
BASE = Base(CFG.base_lat, CFG.base_lon, 1500.0)


def _line(start_bearing, d0, speed, n=20, step=10, t0=1_000_000.0):
    """Üsse `start_bearing` yönündeki d0 mesafesinden `speed` m/s ile üsse doğru giden iz."""
    pts = []
    for i in range(n):
        d = d0 - speed * step * i
        lat, lon = destination(CFG.base_lat, CFG.base_lon, start_bearing, d)
        pts.append((t0 + i * step, lat, lon))
    return pts


def test_approaching_vehicle():
    pts = _line(135, 4000, 9)
    r = analyze_points(pts, BASE, CFG, reference_ts=pts[-1][0])
    assert r["approaching"] is True
    assert 8.5 < r["speed_mps"] < 9.5
    assert abs(r["heading_deg"] - 315) < 3
    assert r["heading_deviation_deg"] < 3
    # kalan mesafe (üs sınırına) / hız
    expected = (r["distance_to_base_m"] - 1500) / 9 / 60
    assert abs(r["eta_min"] - expected) < 0.3


def test_receding_vehicle_not_approaching():
    pts = list(reversed(_line(135, 4000, 9)))
    t0 = 1_000_000.0
    pts = [(t0 + i * 10, p[1], p[2]) for i, p in enumerate(pts)]
    r = analyze_points(pts, BASE, CFG, reference_ts=pts[-1][0])
    assert r["approaching"] is False and r["eta_min"] is None
    assert r["closing_speed_mps"] < 0


def test_stationary_has_no_heading():
    lat, lon = destination(CFG.base_lat, CFG.base_lon, 90, 3000)
    pts = [(1_000_000 + i * 10, lat, lon) for i in range(10)]
    r = analyze_points(pts, BASE, CFG, reference_ts=pts[-1][0])
    assert r["speed_mps"] < 0.1 and r["heading_deg"] is None and r["approaching"] is False


def test_single_point_insufficient():
    r = analyze_points([(1.0, CFG.base_lat, CFG.base_lon + 0.05)], BASE, CFG, reference_ts=1.0)
    assert r["insufficient_data"] is True and r["approaching"] is False


def test_stale_flag():
    pts = _line(135, 4000, 9)
    r = analyze_points(pts, BASE, CFG, reference_ts=pts[-1][0] + 5000)
    assert r["stale"] is True


def test_tracker_associates_and_creates():
    store = TrackStore(CFG)
    t0 = time.time() - 60
    pts = _line(135, 4000, 9, t0=t0)
    store.put_vehicle(Vehicle(id="V-1", cls="car", points=pts))
    # araç 10 sn sonra beklenen yerde; ayrıca uzakta bir başka tespit
    nxt_lat, nxt_lon = destination(CFG.base_lat, CFG.base_lon, 135, 4000 - 9 * 10 * 20)
    far_lat, far_lon = destination(CFG.base_lat, CFG.base_lon, 0, 9000)
    ids = store.associate([{"class": "car", "lat": nxt_lat, "lon": nxt_lon}, {"class": "truck", "lat": far_lat, "lon": far_lon}], ts=pts[-1][0] + 10)
    assert ids[0] == "V-1"
    assert ids[1] == "V-1000"
    assert len(store.points("V-1")) == 21
