import math
import random

from app.classifier import Base, classify, detect_convoy, detect_direct_approach, detect_loitering
from app.config import Settings

P = Settings()
BASE = Base(P.base_lat, P.base_lon, 1500.0)
R = 6371008.8
T_END = 2_000_000.0


def dest(lat, lon, bearing, dist):
    b, d = math.radians(bearing), dist / R
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def approach_track(bearing=135, d_end=2800, speed=9.0, offset=0.0, seconds=600, step=15, lateral=0.0, seed=1):
    rng = random.Random(seed)
    pts = []
    for t in range(-seconds, 1, step):
        d = d_end + offset + speed * (-t)
        lat, lon = dest(P.base_lat, P.base_lon, bearing, d)
        lat, lon = dest(lat, lon, bearing + 90, lateral + rng.gauss(0, 1.5))
        pts.append((T_END + t, lat, lon))
    return pts


def loiter_track(hours=2.0, step=15, seed=2):
    rng = random.Random(seed)
    clat, clon = dest(P.base_lat, P.base_lon, 300, 3600)
    e = n = 0.0
    pts = []
    for t in range(-int(hours * 3600), 1, step):
        e, n = 0.985 * e + rng.gauss(0, 5), 0.985 * n + rng.gauss(0, 5)
        lat, lon = dest(clat, clon, 90, e)
        lat, lon = dest(lat, lon, 0, n)
        pts.append((T_END + t, lat, lon))
    return pts


def random_track(seed=3, seconds=7200, step=15):
    rng = random.Random(seed)
    lat, lon = dest(P.base_lat, P.base_lon, 200, 6500)
    hdg = rng.uniform(0, 360)
    pts = []
    for t in range(-seconds, 1, step):
        hdg += rng.gauss(0, 60)
        sp = rng.uniform(0, 10)
        lat, lon = dest(lat, lon, hdg, sp * step)
        pts.append((T_END + t, lat, lon))
    return pts


def test_direct_approach_detected():
    r = detect_direct_approach(approach_track(), BASE, P)
    assert r and r["pattern"] == "DIRECT_APPROACH"
    assert r["metrics"]["mean_heading_deviation_deg"] < 5
    assert r["confidence"] > 0.8


def test_tangential_motion_is_not_direct_approach():
    # üs etrafında teğet (kerteriz sabit değil) -> mesafe azalmıyor
    pts = []
    for t in range(-600, 1, 15):
        lat, lon = dest(P.base_lat, P.base_lon, 90 + t * 0.02, 4000)
        pts.append((T_END + t, lat, lon))
    assert detect_direct_approach(pts, BASE, P) is None


def test_oblique_approach_over_15deg_rejected():
    # üsse yaklaşıyor ama istikametten ~35° sapmış (yüksek sapma)
    pts = []
    start_lat, start_lon = dest(P.base_lat, P.base_lon, 135, 4000)
    for i, t in enumerate(range(-600, 1, 15)):
        lat, lon = dest(start_lat, start_lon, 315 - 35, 8.0 * 15 * i)
        pts.append((T_END + t, lat, lon))
    r = detect_direct_approach(pts, BASE, P)
    assert r is None


def test_receding_is_not_approach():
    pts = list(reversed(approach_track()))
    pts = [(T_END - 600 + i * 15, p[1], p[2]) for i, p in enumerate(pts)]
    assert detect_direct_approach(pts, BASE, P) is None


def test_loitering_detected_and_short_observation_rejected():
    r = detect_loitering(loiter_track(), P)
    assert r and r["metrics"]["radius_m"] < 200
    assert detect_loitering(loiter_track(hours=0.4), P) is None  # gözlem süresi yetersiz


def test_moving_vehicle_is_not_loitering():
    assert detect_loitering(approach_track(seconds=7200), P) is None


def test_convoy_detected_for_three_vehicles():
    tracks = {"A": approach_track(offset=0, lateral=1.5), "B": approach_track(offset=45, lateral=-1, seed=5), "C": approach_track(offset=90, seed=6)}
    r = detect_convoy(tracks, P)
    assert r and r["vehicles"] == ["A", "B", "C"]
    assert r["metrics"]["mean_pair_gap_m"] < 120


def test_two_vehicles_far_apart_no_convoy():
    tracks = {"A": approach_track(offset=0), "B": approach_track(offset=900, seed=9)}
    assert detect_convoy(tracks, P) is None


def test_opposite_directions_no_convoy():
    up = approach_track(offset=0)
    down = [(T_END - 600 + i * 15, p[1], p[2]) for i, p in enumerate(reversed(approach_track(offset=100, seed=4)))]
    assert detect_convoy({"A": up, "B": down}, P) is None


def test_classify_convoy_takes_priority_and_lists_secondary():
    tracks = {"A": approach_track(offset=0), "B": approach_track(offset=45, seed=5), "C": approach_track(offset=90, seed=6)}
    r = classify(tracks, BASE, P)
    assert r["pattern"] == "CONVOY"
    assert r["involved_vehicles"] == ["A", "B", "C"]
    names = [m["pattern"] for m in r["detail"]["matched_patterns"]]
    assert names == ["CONVOY", "DIRECT_APPROACH"]
    assert r["detail"]["per_vehicle"]["A"]["pattern"] == "CONVOY"


def test_classify_single_direct_approach():
    r = classify({"A": approach_track()}, BASE, P)
    assert r["pattern"] == "DIRECT_APPROACH" and r["involved_vehicles"] == ["A"]


def test_classify_loitering_and_random_mix():
    r = classify({"L": loiter_track(), "R": random_track()}, BASE, P)
    assert r["pattern"] == "LOITERING"
    assert r["detail"]["per_vehicle"]["L"]["pattern"] == "LOITERING"
    assert r["detail"]["per_vehicle"]["R"]["pattern"] == "RANDOM"


def test_classify_random_only():
    r = classify({"R": random_track()}, BASE, P)
    assert r["pattern"] == "RANDOM" and 0.3 <= r["confidence"] <= 0.75


def test_single_point_is_random_with_insufficient_flag():
    r = classify({"X": [(T_END, P.base_lat + 0.05, P.base_lon)]}, BASE, P)
    assert r["pattern"] == "RANDOM" and r["detail"]["insufficient_data"] == ["X"]


# ------------------------------------------------------------------ 5 dk'lık (seyrek) izler — olay veri seti
from app.classifier import effective_params, sampling_step  # noqa: E402


def sparse_approach(offset=0.0, seed=1, points=6, step=300, speed=4.5, bearing=0, d_end=3900, lateral=0.0):
    """5 dk adımlı iz: üsse `bearing` yönünden doğrudan yaklaşır; son nokta T_END."""
    rng = random.Random(seed)
    pts = []
    for i in range(points):
        k = points - 1 - i
        lat, lon = dest(P.base_lat, P.base_lon, bearing, d_end + offset + speed * step * k)
        lat, lon = dest(lat, lon, bearing + 90, lateral + rng.gauss(0, 2))
        pts.append((T_END - k * step, lat, lon))
    return pts


def test_sampling_step_and_effective_params():
    dense = {"A": approach_track(seconds=300)}
    sparse = {"A": sparse_approach()}
    assert sampling_step(dense) == 15 and sampling_step(sparse) == 300
    assert effective_params(P, 15) is P  # yoğun izde hiçbir şey değişmez
    e = effective_params(P, 300)
    assert e.approach_window_s >= 1800 and e.convoy_sample_s == 300 and e.convoy_max_gap_s == 600 and e.convoy_min_samples == 4


def test_sparse_direct_approach_detected_only_with_enough_history():
    assert classify({"A": sparse_approach(points=5)}, BASE, P)["pattern"] == "DIRECT_APPROACH"
    r3 = classify({"A": sparse_approach(points=3)}, BASE, P)
    assert r3["pattern"] == "RANDOM"  # 3 nokta (10 dk) henüz kanıt değil
    assert r3["detail"]["sampling_step_s"] == 300


def test_sparse_convoy_detected():
    tracks = {"A": sparse_approach(offset=0), "B": sparse_approach(offset=30, seed=2), "C": sparse_approach(offset=60, seed=3)}
    r = classify(tracks, BASE, P)
    assert r["pattern"] == "CONVOY" and r["involved_vehicles"] == ["A", "B", "C"]
    assert [m["pattern"] for m in r["detail"]["matched_patterns"]] == ["CONVOY", "DIRECT_APPROACH"]


def test_sparse_vehicles_far_apart_are_not_convoy():
    tracks = {"A": sparse_approach(offset=0), "B": sparse_approach(offset=1500, seed=2)}
    assert classify(tracks, BASE, P)["pattern"] != "CONVOY"


def test_sparse_receding_or_static_is_not_approach():
    receding = [(T_END - (5 - i) * 300, *dest(P.base_lat, P.base_lon, 0, 2000 + 1350 * i)) for i in range(6)]
    assert classify({"R": receding}, BASE, P)["pattern"] == "RANDOM"
    parked = [(T_END - (5 - i) * 300, *dest(P.base_lat, P.base_lon, 90, 3000)) for i in range(6)]
    assert classify({"S": parked}, BASE, P)["pattern"] == "RANDOM"


def test_sparse_loitering_two_hours():
    # 5 dk adımla 2 saat, ~40 m içinde dolanan araç
    rng = random.Random(5)
    clat, clon = dest(P.base_lat, P.base_lon, 90, 2700)
    pts = []
    e = n = 0.0
    for i in range(25):
        e, n = 0.9 * e + rng.gauss(0, 12), 0.9 * n + rng.gauss(0, 12)
        lat, lon = dest(*dest(clat, clon, 90, e), 0, n)
        pts.append((T_END - (24 - i) * 300, lat, lon))
    r = classify({"L": pts}, BASE, P)
    assert r["pattern"] == "LOITERING" and r["detail"]["per_vehicle"]["L"]["metrics"]["LOITERING"]["radius_m"] < 100


def test_sparse_approach_then_stop_keeps_direct_approach_despite_gps_jitter():
    """Üsse doğrudan yaklaşıp durmuş araç: dururken ~9 m'lik GPS gürültüsü 'yön' sayılmamalı."""
    rng = random.Random(11)
    dists = [7200, 5400, 3600, 1800, 900, 900, 900]
    pts = []
    for i, d in enumerate(dists):
        lat, lon = dest(P.base_lat, P.base_lon, 315, d)
        lat, lon = dest(lat, lon, rng.uniform(0, 360), abs(rng.gauss(0, 6)))  # durunca da ~6-9 m titreme
        pts.append((T_END - (len(dists) - 1 - i) * 300, lat, lon))
    r = classify({"S": pts}, BASE, P)
    assert r["pattern"] == "DIRECT_APPROACH", r["detail"]["per_vehicle"]["S"]
    assert effective_params(P, 300).approach_min_segment_m == 15 and effective_params(P, 15).approach_min_segment_m == 5
