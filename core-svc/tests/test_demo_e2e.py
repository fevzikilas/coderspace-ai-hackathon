"""Demo senaryosu: seed + DRN-03 kareleri -> georef -> tracker eşleşmesi -> analiz."""
from fastapi.testclient import TestClient

from app import demo
from app.config import Settings
from app.geo import haversine_m
from app.main import create_app

def _client() -> TestClient:
    return TestClient(create_app(Settings(demo_mode=True, data_dir="")))


DRN03_META = dict(lat=39.881912, lon=32.773585, alt=120.0, heading=315.0, gimbal_pitch=-90.0, fov=84.0, sensor_w=1920, sensor_h=1080)
# detection-svc DEMO_SCENES["DRN-03"] ile aynı kutular (1920x1080 piksel)
def _box(cls, conf, cx, cy, w, h):
    return {"class": cls, "conf": conf, "x1": (cx - w / 2) * 1920, "y1": (cy - h / 2) * 1080, "x2": (cx + w / 2) * 1920, "y2": (cy + h / 2) * 1080}

DEMO_BOXES = [
    _box("pickup", 0.93, 0.4885, 0.1298, 0.0093, 0.0436),
    _box("truck", 0.95, 0.5, 0.5, 0.0116, 0.07),
    _box("pickup", 0.91, 0.493, 0.8702, 0.0093, 0.0436),
]


def test_georef_lands_on_convoy_truth_and_matches_vehicles():
    with _client() as c:
        c.post("/admin/demo/reset")
        r = c.post("/georeference", json={"image_id": "img-x", "boxes": DEMO_BOXES, "drone_meta": DRN03_META, "drone_id": "DRN-03"})
        assert r.status_code == 200, r.text
        body = r.json()
        truth = demo.convoy_now_position(Settings())
        got = {d["vehicle_id"]: (d["lat"], d["lon"]) for d in body["detections"]}
        assert set(got) == {"V-101", "V-102", "V-103"}
        for vid, (lat, lon) in got.items():
            assert haversine_m(lat, lon, *truth[vid]) < 6.0, vid

        # Tracker'a yeni nokta eklendi -> araç hala yaklaşıyor
        a = c.post("/tracks/analyze", json={"vehicle_id": "V-101"}).json()
        assert a["approaching"] is True
        assert 8.0 < a["speed_mps"] < 10.0
        assert a["heading_deviation_deg"] < 10
        assert 2700 < a["distance_to_base_m"] < 2900
        assert 2.0 < a["eta_min"] < 3.2  # (2800-1500)/9 ≈ 2.4 dk

        det = c.get(f"/detections/{body['detection_id']}").json()
        assert det["vehicle_ids"] == ["V-101", "V-102", "V-103"]


def test_tracks_window_and_vehicles_listing():
    with _client() as c:
        c.post("/admin/demo/reset")
        pts = c.get("/tracks/V-101?window=2h").json()["points"]
        assert len(pts) > 400
        short = c.get("/tracks/V-101?window=5m").json()["points"]
        assert 15 < len(short) < 25
        assert c.get("/tracks/NOPE").status_code == 404
        assert c.get("/tracks/V-101?window=abc").status_code == 422

        vs = c.get("/vehicles").json()["vehicles"]
        assert {v["vehicle_id"] for v in vs} == {"V-101", "V-102", "V-103", "V-201", "V-301"}
        v101 = next(v for v in vs if v["vehicle_id"] == "V-101")
        assert v101["analysis"]["approaching"] is True
        assert len(v101["trace"]) <= 80


def test_analyze_with_raw_coords():
    with _client() as c:
        coords = [
            {"lat": 39.90, "lon": 32.80, "ts": 1000},
            {"lat": 39.9, "lon": 32.795, "ts": 1010},
            {"lat": 39.9, "lon": 32.79, "ts": 1020},
        ]
        r = c.post("/tracks/analyze", json={"coords": coords, "base_location": {"lat": 39.9, "lon": 32.75}})
        assert r.status_code == 200 and r.json()["approaching"] is True
        assert c.post("/tracks/analyze", json={}).status_code == 422
