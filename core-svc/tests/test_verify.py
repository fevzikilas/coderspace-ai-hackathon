"""Rapor iddialarının nicel doğrulaması (verify.py). REFERANS AN = çekim anı: rapor konumu aracın GÖRÜNTÜDEKİ konumudur (resmî örnek),
rapor saati yalnızca eskilik denetimi içindir. Senaryolar gerçek rapor şablonlarına benzer."""
import math

import pytest
from fastapi.testclient import TestClient

from app.analysis import Base
from app.config import Settings
from app.geo import from_enu
from app.main import create_app
from app.store import TrackStore, Vehicle
from app.timeutil import clock_to_epoch, iso
from app.verify import Verifier

DATE = "2025-06-01"
T = lambda hhmm: clock_to_epoch(hhmm, DATE)  # noqa: E731
REF = T("13:25")
P0 = (39.9400, 32.8500)                      # duran kamyonun (A) yeri
BASE = from_enu(*P0, 0.0, 6000.0)            # üs, P0'ın 6 km kuzeyinde
CFG = Settings(data_dir="", database_url=None)


def off(lat, lon, de=0.0, dn=0.0):
    return from_enu(lat, lon, de, dn)


def minutes(start, end, step=5):
    h0, m0 = map(int, start.split(":")); h1, m1 = map(int, end.split(":"))
    return [f"{(t // 60):02d}:{t % 60:02d}" for t in range(h0 * 60 + m0, h1 * 60 + m1 + 1, step)]


def still(lat, lon, start="11:25", end="13:25"):
    return [(T(t), lat, lon) for t in minutes(start, end)]


def build(extra=None):
    st = TrackStore(CFG)
    a = [(T(t), *off(*P0, 1.5 * math.sin(i), 1.5 * math.cos(i))) for i, t in enumerate(minutes("11:25", "13:25"))]   # 2 saattir duran kamyon
    start = off(*P0, 80.0, 0.0)
    b = [(T(t), *off(*start, 0.0, 600.0 * i)) for i, t in enumerate(minutes("12:55", "13:25"))]                     # üsse doğru 2 m/s; 13:25'te P0'ın 3.6 km kuzeyinde
    c = [(T(t), *off(*P0, -400.0, -600.0 * i)) for i, t in enumerate(minutes("12:55", "13:25"))]                    # üsten uzaklaşan 2 m/s
    for vid, pts in (("A", a), ("B", b), ("C", c), *(extra or {}).items()):
        st.put_vehicle(Vehicle(id=vid, cls="unknown", points=pts, catalog=True))
    return st, {"A": a[-1][1:], "B": b[-1][1:], "C": c[-1][1:]}


def event(dets, center=P0):
    dlat, dlon = 200.0 / 111320.0, 300.0 / (111320.0 * math.cos(math.radians(center[0])))   # 600 x 400 m ayak izi
    return {
        "ts": REF, "reference_time": iso(REF), "detections": dets,
        "image": {"corner_coordinates": {"top_left": [center[0] + dlat, center[1] - dlon], "top_right": [center[0] + dlat, center[1] + dlon], "bottom_left": [center[0] - dlat, center[1] - dlon], "bottom_right": [center[0] - dlat, center[1] + dlon]}},
    }


def det(cls, vid=None, conf=0.9, pos=P0):
    return {"class": cls, "conf": conf, "lat": pos[0], "lon": pos[1], "vehicle_id": vid}


def vehicle_claim(**kw):
    c = {"kind": "vehicle", "types": [], "count": None, "convoy": False, "baseline": None, "count_relation": None, "motion": None, "min_duration_min": None, "identity": None, "color": None, "hearsay": False, "self_unverified": False, "context_reason": None}
    c.update(kw)
    return c


def run(claim, t="13:05", pos=P0, dets=None, extra=None, center=P0, ref_points_after=None):
    st, _ = build(extra)
    if ref_points_after:
        v = st.get("A")
        v.points.extend(ref_points_after)
    dets = [det("truck", "A")] if dets is None else dets
    v = Verifier(st, CFG, event(dets, center), Base(*BASE, 1500.0))
    return v.verify({"id": "r", "ts": T(t), "lat": pos[0], "lon": pos[1], "claim": claim})


def checks(res):
    return {c["aspect"]: c["result"] for c in res["checks"]}


def b_scene(**kw):
    """Görüntü B'nin ÇEKİM ANI konumunda (üsse yaklaşan araç); rapor konumu = B'nin görüntüdeki konumu."""
    _, pos = build()
    bp = pos["B"]
    return bp, dict(center=bp, dets=[det("car", "B", 0.9, bp)], **kw)


# ---------------------------------------------------------------------------------------------- duran araç (A)
def test_stationary_truck_report_is_compatible():
    r = run(vehicle_claim(types=["truck"], count=1, count_relation="exact", motion="stationary", min_duration_min=30))
    assert r["verdict"] == "compatible" and checks(r) == {"konum": "match", "tip": "match", "sayı": "match", "hareket": "match"}
    assert r["nearest_track"] == "A" and r["nearest_track_m"] < 5 and r["in_image_footprint"] is True and r["motion_track"] == "A"


def test_wrong_vehicle_type_is_incompatible_and_own_detection_wins():
    r = run(vehicle_claim(types=["car"], count=1, count_relation="exact", motion="stationary"))
    assert r["verdict"] == "incompatible" and checks(r)["tip"] == "mismatch"
    assert "truck" in next(c["observed"] for c in r["checks"] if c["aspect"] == "tip")


def test_wrong_count_is_incompatible_when_observable():
    r = run(vehicle_claim(types=["truck"], count=5, count_relation="exact", motion="stationary"))
    assert r["verdict"] == "incompatible" and checks(r)["sayı"] == "mismatch" and "iddiadan az" in next(c["observed"] for c in r["checks"] if c["aspect"] == "sayı")


def test_more_vehicles_than_claimed_is_a_mismatch():
    e = {"E": still(*off(*P0, 30.0, 0.0))}
    r = run(vehicle_claim(types=["truck"], count=1, count_relation="exact"), dets=[det("truck", "A"), det("truck", "E", 0.9, off(*P0, 30.0, 0.0))], extra=e)
    assert checks(r)["sayı"] == "mismatch" and "iddiadan fazla" in next(c["observed"] for c in r["checks"] if c["aspect"] == "sayı")


def test_stationary_window_ends_at_capture_time_and_uses_the_claimed_duration():
    assert checks(run(vehicle_claim(types=["truck"], motion="stationary", min_duration_min=60)))["hareket"] == "match"   # A 2 saattir duruyor
    bp, kw = b_scene()
    bad = run(vehicle_claim(motion="stationary", min_duration_min=30), pos=bp, **kw)  # B hareketli
    assert bad["verdict"] == "incompatible" and checks(bad)["hareket"] == "mismatch"


# ---------------------------------------------------------------------------------------------- konum (çekim anı)
def test_report_location_is_compared_with_the_CAPTURE_time_position_not_the_report_time_position():
    """B, rapor saatinde (13:05) konumdan 2.4 km uzaktaydı; çekim anında (13:25) iddia konumunda. Resmî örnekteki gibi UYUMLU."""
    bp, kw = b_scene()
    r = run(vehicle_claim(types=["car"], motion="toward_base"), t="13:05", pos=bp, **kw)
    assert checks(r)["konum"] == "match" and checks(r)["hareket"] == "match" and r["verdict"] == "compatible"


def test_no_vehicle_at_the_claimed_place_inside_the_footprint_is_a_mismatch_only_for_fresh_reports():
    p = off(*P0, 260.0, 200.0)
    fresh = run(vehicle_claim(types=["truck"], motion="moving"), t="13:15", pos=p, dets=[det("truck", "A")])
    assert fresh["verdict"] == "incompatible" and checks(fresh)["konum"] == "mismatch"
    old = run(vehicle_claim(types=["truck"], motion="moving"), t="12:00", pos=p, dets=[det("truck", "A")])  # 85 dk eski: araç gitmiş olabilir
    assert old["verdict"] == "unverifiable" and "aracın gitmiş olması" in old["checks"][0]["observed"]


def test_outside_the_footprint_and_no_track_is_irrelevant_to_this_image_not_refuted():
    r = run(vehicle_claim(types=["truck"], motion="stationary"), pos=off(*P0, 2000.0, 0.0))
    assert r["verdict"] == "irrelevant" and "başka bir çekimdeki" in r["summary"]


def test_untracked_parked_vehicle_is_confirmed_by_detection_but_its_motion_is_unverifiable():
    p = off(*P0, 220.0, 10.0)
    r = run(vehicle_claim(types=["van"], count=1, count_relation="exact", motion="stationary"), pos=p, dets=[det("truck", "A"), det("van", None, 0.85, p)])
    c = checks(r)
    assert c["konum"] == "match" and c["tip"] == "match" and c["sayı"] == "match" and c["hareket"] == "unverifiable"
    assert "izsiz: hareket verisi YOK" in next(x["observed"] for x in r["checks"] if x["aspect"] == "hareket")
    assert r["motion_track"] is None  # A (220 m uzakta) yanlışlıkla kullanılmadı


def test_low_confidence_untracked_detection_is_not_evidence_of_a_vehicle():
    p = off(*P0, 270.0, 10.0)
    r = run(vehicle_claim(types=["van"], motion="stationary"), t="13:15", pos=p, dets=[det("truck", "A"), det("van", None, 0.30, p)])
    assert checks(r)["konum"] == "mismatch"


# ---------------------------------------------------------------------------------------------- kimlik / hareket yönü
def test_friendly_identity_claim_is_unverifiable_and_never_changes_the_described_fact():
    bp, kw = b_scene()
    r = run(vehicle_claim(types=["car"], motion="toward_base", identity="friendly"), pos=bp, **kw)
    assert checks(r)["hareket"] == "match" and checks(r)["kimlik"] == "unverifiable" and r["verdict"] == "compatible" and "kimlik" in r["unverified"]
    assert "'dost' iddiası bunu değiştirmez" in r["summary"]


def test_away_claim_about_an_approaching_vehicle_is_incompatible():
    bp, kw = b_scene()
    r = run(vehicle_claim(types=["car"], motion="away", identity="friendly"), pos=bp, **kw)
    assert r["verdict"] == "incompatible" and checks(r)["hareket"] == "mismatch"


def test_approaching_claim_about_a_parked_car_is_the_decoy_pattern():
    """Gerçek veride 'planlı ikmal/bize bağlı, üsse ilerleyen otomobil' tuzağı: konumdaki araç aslında duruyor."""
    r = run(vehicle_claim(types=["car"], motion="toward_base", identity="friendly"), dets=[det("car", "A", 0.94)])
    assert r["verdict"] == "incompatible" and checks(r)["hareket"] == "mismatch" and checks(r)["konum"] == "match"


def test_report_older_than_the_two_hour_window_is_unverifiable():
    r = run(vehicle_claim(types=["truck"], motion="stationary"), t="10:30")
    assert r["verdict"] == "unverifiable" and checks(r) == {"zaman": "unverifiable"}


def test_no_future_data_is_used():
    """Çekim anından SONRAKİ iz noktaları (aracı başka yere götürür) doğrulamayı etkilemez."""
    after = [(T("13:30"), *off(*P0, 5000.0, 0.0)), (T("13:35"), *off(*P0, 6000.0, 0.0))]
    r = run(vehicle_claim(types=["truck"], count=1, count_relation="exact", motion="stationary"), ref_points_after=after)
    assert r["verdict"] == "compatible" and checks(r)["hareket"] == "match"


# ---------------------------------------------------------------------------------------------- sayı gözlenebilirliği
def test_a_tracked_vehicle_missed_by_the_detector_widens_the_observed_count_range():
    e = {"E": still(*off(*P0, 30.0, 0.0))}       # izli ama bu görüntüde tespit edilemedi → sınıfı bilinmiyor
    kw = dict(dets=[det("truck", "A")], extra=e)
    assert checks(run(vehicle_claim(types=["truck"], count=2, count_relation="exact"), **kw))["sayı"] == "match"       # [1, 2] aralığında
    r3 = run(vehicle_claim(types=["truck"], count=3, count_relation="exact"), **kw)
    assert checks(r3)["sayı"] == "mismatch" and "iddiadan az" in next(c["observed"] for c in r3["checks"] if c["aspect"] == "sayı")


def test_outside_the_footprint_a_nearby_track_does_not_make_the_report_about_this_image():
    far = off(*P0, 1500.0, 0.0)
    r = run(vehicle_claim(types=["truck"], count=5, count_relation="exact", motion="stationary"), pos=far, dets=[det("truck", "A")], extra={"D": still(*far)})
    assert r["verdict"] == "irrelevant" and r["in_image_footprint"] is False


def test_count_unverifiable_when_location_itself_is_unverified():
    r = run(vehicle_claim(types=["truck"], count=3, count_relation="exact", motion="stationary"), t="12:00", pos=off(*P0, 260.0, 200.0))  # ayak izi içi, boş, 85 dk eski
    assert checks(r)["konum"] == "unverifiable" and checks(r)["sayı"] == "unverifiable" and r["verdict"] == "unverifiable"


def test_density_claim_inside_the_footprint():
    assert checks(run(vehicle_claim(count_relation="more_than_usual", baseline=4)))["yoğunluk"] == "mismatch"  # yalnızca 1 araç
    many = {f"X{i}": still(*off(*P0, 20.0 * i, 0.0)) for i in range(1, 6)}
    dets = [det("truck", "A")] + [det("car", f"X{i}", 0.9, off(*P0, 20.0 * i, 0.0)) for i in range(1, 6)]
    assert checks(run(vehicle_claim(count_relation="more_than_usual", baseline=4), dets=dets, extra=many))["yoğunluk"] == "match"


# ---------------------------------------------------------------------------------------------- bölge düzeyi + bağlam
def test_zone_no_heavy_and_zone_activity_and_staleness():
    heavy = run({"kind": "zone_no_heavy", "types": ["truck", "bus"]}, t="13:20")
    assert heavy["verdict"] == "incompatible" and "1 ağır araç" in heavy["checks"][0]["observed"]
    assert run({"kind": "zone_no_heavy", "types": ["truck", "bus"]}, t="13:20", dets=[det("car", "B")])["verdict"] == "compatible"
    stale = run({"kind": "zone_no_heavy", "types": ["truck", "bus"]}, t="11:55")
    assert stale["verdict"] == "unverifiable" and "eski" in stale["checks"][0]["observed"]
    assert run({"kind": "zone_activity", "motion": "normal"}, t="13:20", dets=[det("truck", "A")])["verdict"] == "compatible"
    busy = run({"kind": "zone_activity", "motion": "normal"}, t="13:20", dets=[det("car", "B")])
    assert busy["verdict"] == "incompatible" and "üsse yaklaşıyor" in busy["checks"][0]["observed"]


def test_context_reports_are_irrelevant():
    r = run({"kind": "context", "context_reason": "hava durumu"})
    assert r["verdict"] == "irrelevant" and "hava durumu" in r["summary"]


def test_endpoint_roundtrip_and_rejects_reports_from_the_future(tmp_path):
    (tmp_path / "image_meta.json").write_text(
        '{"img_1": {"width_px": 960, "height_px": 540, "capture_time": "13:25", "corner_coordinates": {"top_left": [39.9410, 32.8490], "top_right": [39.9410, 32.8510], "bottom_left": [39.9390, 32.8490], "bottom_right": [39.9390, 32.8510]}}}')
    (tmp_path / "tracks.csv").write_text("track_id,time,lat,lon\nA,13:20,39.94000,32.85000\nA,13:25,39.94000,32.85001\n")
    with TestClient(create_app(Settings(data_dir=str(tmp_path), database_url=None))) as c:
        g = c.post("/georeference", json={"image_id": "img_1", "boxes": [{"class": "truck", "conf": 0.9, "x1": 470, "y1": 250, "x2": 490, "y2": 290}], "match_tracks": True, "ingest": False}).json()
        body = {"base_location": {"lat": BASE[0], "lon": BASE[1]}, "claims": [
            {"id": "a", "ts": "13:20", "lat": 39.94000, "lon": 32.85000, "claim": vehicle_claim(types=["truck"], count=1, count_relation="exact")},
            {"id": "b", "ts": "13:20", "claim": {"kind": "context", "context_reason": "hava"}}]}
        r = c.post(f"/detections/{g['detection_id']}/verify-claims", json=body)
        assert r.status_code == 200
        out = {x["id"]: x["verdict"] for x in r.json()["results"]}
        assert out == {"a": "compatible", "b": "irrelevant"} and r.json()["counts"] == {"compatible": 1, "irrelevant": 1}
        body["claims"][0]["ts"] = "13:30"  # olay anından sonra
        assert c.post(f"/detections/{g['detection_id']}/verify-claims", json=body).status_code == 422
        assert c.post("/detections/yok/verify-claims", json=body).status_code == 404
