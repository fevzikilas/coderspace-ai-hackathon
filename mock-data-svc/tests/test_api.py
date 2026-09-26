from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_drones_has_eight_with_contract_fields():
    r = client.get("/drones")
    assert r.status_code == 200
    drones = r.json()["drones"]
    assert len(drones) == 8
    for d in drones:
        for key in ("id", "lat", "lon", "heading", "fov", "status"):
            assert key in d


def test_demo_drone_is_static():
    a = client.get("/drones").json()["drones"]
    d3 = next(d for d in a if d["id"] == "DRN-03")
    assert (d3["lat"], d3["lon"], d3["heading"]) == (39.881912, 32.773585, 315.0)
    assert d3["gimbal_pitch"] == -90.0


def test_intel_is_always_low_confidence():
    r = client.get("/intel/ZONE-ALPHA")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 3
    assert all(i["confidence"] == "low" for i in items)
    assert all({"text", "source", "ts"} <= set(i) for i in items)


def test_reports_contract():
    items = client.get("/reports/zone-alpha").json()["items"]
    assert items and all({"text", "reporter", "ts"} <= set(i) for i in items)


def test_unknown_zone_404():
    assert client.get("/intel/NOPE").status_code == 404
    assert client.get("/reports/NOPE").status_code == 404
