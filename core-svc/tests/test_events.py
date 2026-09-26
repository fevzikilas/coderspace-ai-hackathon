"""Olay (event snapshot) akışı: köşe koordinatlı georef + iz eşleştirme + reference_time bazlı analiz."""
import json
import math

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.georef import pixel_to_ground_corners
from app.geo import haversine_m
from app.main import create_app

R = 6371008.8
BASE = (39.92184, 32.85306)
BASE_LOC = {"lat": BASE[0], "lon": BASE[1], "radius_m": 1500}
W, H = 960, 540


def dest(lat, lon, bearing, dist):
    b, d = math.radians(bearing), dist / R
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_dataset(tmp_path, speed=3.0, n_steps=9, capture_min=14 * 60 + 10):
    """T0001: kuzeyden (9 km) üsse 3 m/s ile yaklaşır; 5 dk adım; capture anından SONRA da noktalar var."""
    rows = ["track_id,time,lat,lon"]
    first = capture_min - 5 * 4  # olaydan 4 adım önce başlar (5 nokta) — olay anı = 5. nokta
    truth = {}
    for k in range(n_steps):
        minute = first + 5 * k
        lat, lon = dest(*BASE, 0.0, 9000 - speed * 300 * k)
        truth[minute] = (lat, lon)
        rows.append(f"T0001,{hhmm(minute)},{lat:.6f},{lon:.6f}")
    (tmp_path / "tracks.csv").write_text("\n".join(rows) + "\n")
    cx, cy = truth[capture_min]
    half_w_deg = (75 / (111320 * math.cos(math.radians(cx))))
    half_h_deg = 42 / 111320
    meta = {
        "img_000001": {
            "width_px": W, "height_px": H, "capture_time": hhmm(capture_min),
            "corner_coordinates": {
                "top_left": [cx + half_h_deg, cy - half_w_deg], "top_right": [cx + half_h_deg, cy + half_w_deg],
                "bottom_left": [cx - half_h_deg, cy - half_w_deg], "bottom_right": [cx - half_h_deg, cy + half_w_deg],
            },
        }
    }
    (tmp_path / "image_meta.json").write_text(json.dumps(meta))
    return truth


@pytest.fixture
def client(tmp_path):
    build_dataset(tmp_path)
    with TestClient(create_app(Settings(data_dir=str(tmp_path), demo_mode=False, database_url=None))) as c:
        yield c


def test_corner_georef_center_and_extremes():
    tl, tr, bl = (10.001, 20.0), (10.001, 20.002), (10.0, 20.0)
    assert pixel_to_ground_corners(0, 0, tl, tr, bl, 100, 50) == pytest.approx(tl)
    assert pixel_to_ground_corners(100, 50, tl, tr, bl, 100, 50) == pytest.approx((10.0, 20.002))  # sağ alt piksel = (sol_alt.enlem, sağ_üst.boylam)
    assert pixel_to_ground_corners(50, 25, tl, tr, bl, 100, 50) == pytest.approx((10.0005, 20.001))


def test_catalog_and_health(client):
    h = client.get("/health").json()
    assert h["dataset"] == {"images": 1, "tracks": 1} and h["demo_mode"] is False
    cat = client.get("/dataset/images").json()
    img = cat["images"][0]
    assert img["image_id"] == "img_000001" and img["capture_time"] == "14:10" and img["capture_iso"] == "2025-06-01T14:10:00Z"
    assert 140 < img["footprint_m"]["width"] < 160 and 80 < img["footprint_m"]["height"] < 90
    assert client.get("/dataset/images/nope").status_code == 404
    assert client.get("/dataset/tracks").json()["tracks"][0]["n_points"] == 9


def test_event_georef_matches_track_and_analysis_is_reference_time_based(client):
    # bbox tam merkezde -> konum = iz noktası (14:10)
    box = {"class": "pickup", "conf": 0.9, "x1": 470, "y1": 250, "x2": 490, "y2": 290}
    r = client.post("/georeference", json={"image_id": "img_000001", "boxes": [box, {"class": "car", "conf": 0.8, "x1": 20, "y1": 20, "x2": 40, "y2": 50}], "match_tracks": True, "ingest": False}).json()
    assert r["georef_method"] == "corners" and r["reference_time"] == "2025-06-01T14:10:00Z" and r["capture_time"] == "14:10"
    d0, d1 = r["detections"]
    assert d0["vehicle_id"] == "T0001" and d0["match_distance_m"] < 2.0
    assert d1["vehicle_id"] is None  # köşedeki izsiz araç: eşleşmedi
    assert r["ingested"] is False

    ev = client.get(f"/detections/{r['detection_id']}").json()
    assert ev["vehicle_ids"] == ["T0001"] and ev["reference_time"] == "2025-06-01T14:10:00Z"

    # analiz: reference_time=14:10 -> son nokta 14:10; gelecek (14:15+) noktaları kullanılmaz
    a = client.post("/tracks/analyze", json={"vehicle_id": "T0001", "base_location": BASE_LOC, "reference_time": "2025-06-01T14:10:00Z"}).json()
    assert a["approaching"] is True and 2.5 < a["speed_mps"] < 3.5
    assert a["last_seen"] == "2025-06-01T14:10:00Z" and a["stale"] is False and a["points_used"] == 4
    assert 5200 < a["distance_to_base_m"] < 5600  # 9000 - 4*900 = 5400 m
    # aynı analiz "HH:MM" ile de istenebilir
    a2 = client.post("/tracks/analyze", json={"vehicle_id": "T0001", "base_location": BASE_LOC, "reference_time": "14:10"}).json()
    assert a2["distance_to_base_m"] == a["distance_to_base_m"]
    # daha geç referans: daha yakın (ve gelecekteki nokta kullanılır)
    later = client.post("/tracks/analyze", json={"vehicle_id": "T0001", "base_location": BASE_LOC, "reference_time": "14:30"}).json()
    assert later["distance_to_base_m"] < a["distance_to_base_m"] - 3000


def test_tracks_until_filters_future_and_window_is_relative_to_reference(client):
    all_pts = client.get("/tracks/T0001?window=2h").json()["points"]
    cut = client.get("/tracks/T0001?window=2h&until=14:10").json()
    assert len(all_pts) == 9 and cut["until"] == "2025-06-01T14:10:00Z"
    assert cut["points"][-1]["ts"] == "2025-06-01T14:10:00Z" and len(cut["points"]) == 5
    win = client.get("/tracks/T0001?window=10m&until=14:10").json()["points"]
    assert [p["ts"][11:16] for p in win] == ["14:00", "14:05", "14:10"]
    assert client.get("/tracks/T0001?until=zz").status_code == 422


def test_current_position_appended_when_track_is_stale(client):
    # capture 14:12 (izin son noktası 14:10): görüntüden gelen güncel konum son nokta olur
    a = client.post("/tracks/analyze", json={"vehicle_id": "T0001", "base_location": BASE_LOC, "reference_time": "14:12",
                                             "current_position": {"lat": 39.9, "lon": 32.85306}}).json()
    assert a["last_seen"] == "2025-06-01T14:12:00Z" and a["last_position"]["lat"] == 39.9


def test_inline_image_meta_and_missing_inputs(client):
    body = {"image_id": "uploaded", "boxes": [{"class": "car", "conf": 0.9, "x1": 0, "y1": 0, "x2": 10, "y2": 10}],
            "image_meta": {"width_px": 100, "height_px": 100, "capture_time": "10:00",
                           "corner_coordinates": {"top_left": [1.001, 2.0], "top_right": [1.001, 2.001], "bottom_left": [1.0, 2.0], "bottom_right": [1.0, 2.001]}}}
    r = client.post("/georeference", json=body).json()
    assert r["georef_method"] == "corners" and r["reference_time"] == "2025-06-01T10:00:00Z"
    assert client.post("/georeference", json={"image_id": "bilinmeyen", "boxes": []}).status_code == 422
    body["image_meta"]["capture_time"] = None
    assert client.post("/georeference", json=body).status_code == 422  # reference_time yok


def test_demo_reset_is_guarded_and_keeps_catalog(tmp_path):
    build_dataset(tmp_path)
    with TestClient(create_app(Settings(data_dir=str(tmp_path), demo_mode=False))) as off:
        assert off.post("/admin/demo/reset").status_code == 403
    with TestClient(create_app(Settings(data_dir=str(tmp_path), demo_mode=True))) as on:
        ids = {v["vehicle_id"] for v in on.get("/vehicles").json()["vehicles"]}
        assert {"T0001", "V-101"} <= ids
        assert on.post("/admin/demo/reset").status_code == 200
        ids2 = {v["vehicle_id"] for v in on.get("/vehicles").json()["vehicles"]}
        assert "T0001" in ids2  # veri seti izleri demo sıfırlamasında silinmez


def test_bad_dataset_files_stop_the_service_with_a_clear_error(tmp_path):
    """Bozuk veri sessizce yutulmaz: servis açılmaz ve hata dosya/alan yolunu söyler."""
    from app.loaders.image_meta import DatasetError

    (tmp_path / "image_meta.json").write_text("bu json degil")
    (tmp_path / "tracks.csv").write_text("a,b\n1,2\n")
    with pytest.raises(DatasetError, match="image_meta.json: geçersiz JSON"):
        with TestClient(create_app(Settings(data_dir=str(tmp_path)))):
            pass
    with pytest.raises(DatasetError, match="DATA_DIR bulunamadı"):
        with TestClient(create_app(Settings(data_dir="/olmayan/dizin"))):
            pass
    # DATA_DIR verilmemişse (eski/demo akışı) servis boş veri setiyle açılır
    with TestClient(create_app(Settings(data_dir=""))) as c:
        assert c.get("/health").json()["dataset"]["images"] == 0
