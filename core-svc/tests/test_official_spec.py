"""Resmî spesifikasyon (gorev_tanimi.pdf) regresyon testleri — gerçek dosyalardan bağımsız, temsilî sayılarla."""
import inspect

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.georef import bbox_center, pixel_to_ground_corners
from app.main import create_app

# PDF "Uçtan uca örnek": img_000123, 1360x765, çekim 13:25
TL, TR, BL, BR = (39.94510, 32.86200), (39.94510, 32.86519), (39.94373, 32.86200), (39.94373, 32.86519)
W, H = 1360, 765
XYWH = (610, 380, 60, 28)  # kamyon kutusu (x, y, w, h)


def test_official_example_pixel_to_coordinate():
    x, y, w, h = XYWH
    cx, cy = bbox_center(x, y, x + w, y + h)
    assert (cx, cy) == (640, 394)  # merkez piksel: x + w/2, y + h/2
    lat, lon = pixel_to_ground_corners(cx, cy, TL, TR, BL, W, H)
    assert round(lon, 5) == 32.86350 and round(lat, 5) == 39.94439  # PDF'teki sonuç


def test_formula_is_two_independent_linear_axes():
    """boylam YALNIZCA üst kenardan, enlem YALNIZCA sol kenardan gelir (tam bilinear değil)."""
    lat, lon = pixel_to_ground_corners(400, 300, TL, TR, BL, W, H)
    assert lon == pytest.approx(TL[1] + 400 / W * (TR[1] - TL[1]), abs=1e-12)
    assert lat == pytest.approx(TL[0] + 300 / H * (BL[0] - TL[0]), abs=1e-12)
    # Köşeler eksene paralel olmasa bile: sağ_üst.enlem ve sol_alt.boylam sonucu ETKİLEMEZ (formülde yoklar)
    lat2, lon2 = pixel_to_ground_corners(400, 300, TL, (TR[0] + 0.01, TR[1]), (BL[0], BL[1] + 0.01), W, H)
    assert (lat2, lon2) == (lat, lon)


def test_bottom_right_is_not_an_input_of_the_formula():
    params = list(inspect.signature(pixel_to_ground_corners).parameters)
    assert not any("bottom_right" in p or p == "br" for p in params), params


def _post(client, bottom_right):
    body = {
        "image_meta": {"width_px": W, "height_px": H, "capture_time": "13:25",
                       "corner_coordinates": {"top_left": TL, "top_right": TR, "bottom_left": BL, "bottom_right": bottom_right}},
        "image_id": "img_000123",
        "boxes": [{"class": "truck", "conf": 0.9, "x1": 610, "y1": 380, "x2": 670, "y2": 408}],
        "ingest": False,
    }
    return client.post("/georeference", json=body).json()["detections"][0]


def test_bottom_right_does_not_change_georeference_endpoint_result():
    with TestClient(create_app(Settings(data_dir="", database_url=None))) as c:
        a, b = _post(c, BR), _post(c, (39.90000, 32.90000))  # bottom_right'ı alakasız bir yere taşı
        assert (a["lat"], a["lon"]) == (b["lat"], b["lon"])
        assert (round(a["lat"], 5), round(a["lon"], 5)) == (39.94439, 32.86350)


# ---------------------------------------------------------------------------------------------- iz eşleştirme (madde 2)
from app.store import TrackStore, Vehicle, match_detections, row_at  # noqa: E402
from app.timeutil import clock_to_epoch  # noqa: E402

DATE = "2025-06-01"
T = lambda hhmm: clock_to_epoch(hhmm, DATE)  # noqa: E731
DEG_PER_M = 1 / 111_320.0  # enlemde 1 m


def _store(tracks: dict[str, list[tuple[str, float, float]]]) -> TrackStore:
    st = TrackStore(Settings(data_dir="", database_url=None))
    for tid, pts in tracks.items():
        st.put_vehicle(Vehicle(id=tid, cls="unknown", points=[(T(t), la, lo) for t, la, lo in pts], catalog=True))
    return st


def test_row_at_is_exact_only():
    pts = [(T("13:20"), 1.0, 1.0), (T("13:25"), 2.0, 2.0)]
    assert row_at(pts, T("13:25")) == pts[1]
    assert row_at(pts, T("13:22")) is None  # ne enterpolasyon ne "en yakın satır"
    assert row_at(pts, T("13:26")) is None


def test_exact_time_row_is_used_not_a_closer_row_of_the_same_track_at_another_time():
    """Aynı izde 13:20 satırı tespite 0 m, 13:25 satırı 8 m. Resmî kural: time == capture_time satırı (8 m) seçilir."""
    lat0, lon0 = 39.9440, 32.8630
    st = _store({"A": [("13:20", lat0, lon0), ("13:25", lat0 + 8 * DEG_PER_M, lon0)]})
    (m,) = match_detections(st, [(lat0, lon0)], T("13:25"), gate_m=15, max_extrap_s=600)
    assert m is not None and m.method == "exact"
    assert m.distance_m == pytest.approx(8.0, abs=0.2)  # 13:20 satırı (0 m) seçilmedi


def test_track_without_a_row_at_capture_time_is_not_a_candidate_even_if_extrapolation_would_be_closer():
    """B izi 13:20'de bitiyor (başka görüntünün çekim anı); ekstrapolasyonla 13:25'te tespite 1 m kalırdı. A'nın 13:25 satırı 9 m.
    ESKİ davranış (position_at) B'yi seçerdi; resmî kural A'yı seçer."""
    lat0, lon0 = 39.9440, 32.8630
    b_prev = ("13:15", lat0 - 120 * DEG_PER_M, lon0)
    b_last = ("13:20", lat0 - 60 * DEG_PER_M, lon0)  # kuzeye 60 m / 5 dk = 0.2 m/s; 300 s ekstrapolasyon → tam 13:25'te lat0 (0 m)
    st = _store({"A": [("13:20", lat0, lon0 + 100 * DEG_PER_M), ("13:25", lat0 + 9 * DEG_PER_M, lon0)], "B": [b_prev, b_last]})
    assert row_at(st.get("B").points, T("13:25")) is None
    (m,) = match_detections(st, [(lat0, lon0)], T("13:25"), gate_m=15, max_extrap_s=600)
    assert m is not None and m.vehicle_id == "A" and m.method == "exact" and m.distance_m == pytest.approx(9.0, abs=0.2)


def test_no_match_within_gate_is_normal_not_an_error():
    lat0, lon0 = 39.9440, 32.8630
    st = _store({"A": [("13:25", lat0 + 100 * DEG_PER_M, lon0)]})  # 100 m uzakta
    assert match_detections(st, [(lat0, lon0)], T("13:25"), gate_m=15, max_extrap_s=600) == [None]


def test_greedy_one_to_one_nearest():
    lat0, lon0 = 39.9440, 32.8630
    st = _store({"A": [("13:25", lat0, lon0)], "B": [("13:25", lat0 + 6 * DEG_PER_M, lon0)]})
    # iki tespit: biri A'ya 0 m, diğeri de A'ya en yakın (2 m) ama A alındığı için B'ye (4 m) gider
    ms = match_detections(st, [(lat0, lon0), (lat0 + 2 * DEG_PER_M, lon0)], T("13:25"), gate_m=15, max_extrap_s=600)
    assert [m.vehicle_id for m in ms] == ["A", "B"]


def test_fallback_to_interpolation_only_when_no_track_has_a_row_at_capture_time():
    lat0, lon0 = 39.9440, 32.8630
    st = _store({"A": [("13:20", lat0 - 30 * DEG_PER_M, lon0), ("13:30", lat0 + 30 * DEG_PER_M, lon0)]})
    # 13:25'te HİÇBİR izin satırı yok (izler başka ızgarada) -> yedek yol: ts'e kadarki noktalardan konum
    (m,) = match_detections(st, [(lat0 - 30 * DEG_PER_M, lon0)], T("13:25"), gate_m=60, max_extrap_s=600)
    assert m is not None and m.method == "interpolated"


def test_georeference_endpoint_reports_match_method(tmp_path):
    (tmp_path / "image_meta.json").write_text(
        '{"img_000123": {"width_px": 1360, "height_px": 765, "capture_time": "13:25", "corner_coordinates": '
        '{"top_left": [39.9451, 32.862], "top_right": [39.9451, 32.86519], "bottom_left": [39.94373, 32.862], "bottom_right": [39.94373, 32.86519]}}}'
    )
    (tmp_path / "tracks.csv").write_text("track_id,time,lat,lon\nT0187,13:20,39.94436,32.86340\nT0187,13:25,39.94441,32.86353\nT0188,13:20,39.94439,32.86350\n")
    with TestClient(create_app(Settings(data_dir=str(tmp_path), database_url=None))) as c:
        r = c.post("/georeference", json={"image_id": "img_000123", "boxes": [{"class": "truck", "conf": 0.9, "x1": 610, "y1": 380, "x2": 670, "y2": 408}], "match_tracks": True, "ingest": False}).json()
        d = r["detections"][0]
        # T0188'in 13:20 satırı tespite T0187'nin 13:25 satırından YAKIN olsa da 13:25 filtresi onu eler
        assert d["vehicle_id"] == "T0187" and d["match_method"] == "exact" and d["match_distance_m"] < 4.0  # PDF: "yaklaşık 2,5 m" (bu sayılarla 3.0 m)
