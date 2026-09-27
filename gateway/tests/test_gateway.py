"""Gateway testleri: alt servisler httpx.MockTransport ile taklit edilir."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as gw

BASE = {"lat": 39.9, "lon": 32.75, "radius_m": 1500}
DRONE = {"id": "DRN-03", "lat": 39.88, "lon": 32.77, "heading": 315.0, "fov": 84.0, "status": "ACTIVE", "alt": 120.0, "gimbal_pitch": -90.0, "sensor_w": 1920, "sensor_h": 1080}
BOXES = [
    {"class": "pickup", "conf": 0.9, "x1": 100.0, "y1": 100.0, "x2": 120.0, "y2": 150.0},
    {"class": "truck", "conf": 0.95, "x1": 500.0, "y1": 500.0, "x2": 530.0, "y2": 580.0},
]
ANALYSIS = {"speed_mps": 9.0, "heading_deg": 315.0, "approaching": True, "eta_min": 2.4, "distance_to_base_m": 2800.0,
            "closing_speed_mps": 9.0, "stale": False, "last_seen": "2025-06-01T14:10:00Z", "last_position": {"lat": 39.95, "lon": 32.85}}
IMG = {"image_id": "img_000860", "capture_time": "14:10", "capture_iso": "2025-06-01T14:10:00Z", "width_px": 960, "height_px": 540,
       "center": {"lat": 39.9253, "lon": 32.8714}, "footprint_m": {"width": 120.0, "height": 67.0},
       "corner_coordinates": {"top_left": [39.925651, 32.870729], "top_right": [39.925651, 32.872131], "bottom_left": [39.925045, 32.870729], "bottom_right": [39.925045, 32.872131]}}
DS_BASE = {"name": "Merkez Us", "lat": 39.92184, "lon": 32.85306, "radius_m": 1500, "alert_radius_m": 8000, "source": "dataset"}
DS_ZONES = {"source": "dataset", "base": DS_BASE, "zones": [{"zone_id": "kuzey-yolu", "name": "Kuzey Yolu", "center": {"lat": 39.95, "lon": 32.85}}, {"zone_id": "dogu-yolu", "name": "Dogu Yolu", "center": {"lat": 39.92, "lon": 32.89}}]}
ASSESSMENT = {"assessment_id": "asm-1", "risk_level": "HIGH", "confidence": 0.9, "mode": "rule-based", "tool_calls_log": [{"tool": "x"}], "evidence_breakdown": [{"source": "movement"}]}


class World:
    def __init__(self, **over):
        self.over = over
        self.calls: list[str] = []
        self.requests: dict[str, dict] = {}  # key -> {"params": ..., "body": ...} (son istek)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} :{request.url.port}{request.url.path}"
        self.calls.append(key)
        body = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = None
        self.requests[key] = {"params": dict(request.url.params), "body": body}
        if (o := self.over.get(key)) is not None:
            return o
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "x"})
        if key == "GET :8004/drones":
            return httpx.Response(200, json={"drones": [DRONE, {**DRONE, "id": "DRN-08", "status": "OFFLINE"}]})
        if key == "POST :8001/detect":
            image_id = (body or {}).get("image_id") or "img-1"
            return httpx.Response(200, json={"image_id": image_id, "drone_id": "DRN-03", "timestamp": "t", "boxes": BOXES, "image_width": 1920, "image_height": 1080, "mode": "mock", "inference_ms": 1.0})
        if key == "POST :8001/appearance/embed":
            tracks = []
            for crop in body["crops"]:
                vector = [1.0, 0.0] if crop["track_id"] == "V-101" else [0.0, 1.0]
                tracks.append({"track_id": crop["track_id"], "embedding": vector, "quality": {"score": 0.8}, "model": "test-model", "crop": {"image_id": body["image_id"], "bbox": crop["bbox"]}})
            return httpx.Response(200, json={"image_id": body["image_id"], "model": "test-model", "tracks": tracks})
        if key == "POST :8002/georeference":
            ref = "2025-06-01T14:15:00Z" if body.get("image_id") == "img_000861" else "2025-06-01T14:10:00Z"
            return httpx.Response(200, json={"detection_id": "det-1", "image_id": body.get("image_id") or "img-1", "timestamp": "t", "reference_time": ref, "georef_method": "corners", "detections": [
                {"class": "pickup", "conf": 0.9, "lat": 39.88, "lon": 32.77, "vehicle_id": "V-101", "box_index": 0},
                {"class": "truck", "conf": 0.95, "lat": 39.881, "lon": 32.771, "vehicle_id": "V-102", "box_index": 1}], "skipped": []})
        if path.startswith("/tracks/") and request.method == "GET":
            return httpx.Response(200, json={"vehicle_id": path.split("/")[-1], "class": "pickup", "points": [{"lat": 39.88, "lon": 32.77, "ts": "2026-01-01T00:00:00Z"}] * 300})
        if key == "POST :8002/tracks/analyze":
            return httpx.Response(200, json=ANALYSIS)
        if key == "POST :8003/pattern/classify":
            ids = json.loads(request.content)["vehicle_ids"]
            return httpx.Response(200, json={"pattern": "CONVOY", "confidence": 0.95, "involved_vehicles": ids, "detail": {"per_vehicle": {v: {"pattern": "CONVOY", "matched": ["CONVOY"]} for v in ids}}})
        if key == "GET :8002/vehicles":
            return httpx.Response(200, json={"base": BASE, "vehicles": [{"vehicle_id": "V-101", "class": "pickup", "trace": [], "last_position": {"lat": 39.88, "lon": 32.77}, "analysis": ANALYSIS}, {"vehicle_id": "V-201", "class": "pickup", "trace": [], "last_position": {"lat": 39.9, "lon": 32.7}, "analysis": ANALYSIS}]})
        if key == "POST :8005/assess":
            return httpx.Response(200, json=ASSESSMENT)
        if key == "POST :8002/admin/demo/reset":
            return httpx.Response(200, json={"anchor": "2026-01-01T00:00:00Z", "vehicles": ["V-101"]})
        if path.startswith("/intel/"):
            return httpx.Response(200, json={"items": [{"text": "i", "source": "s", "ts": "t", "confidence": "low"}]})
        if path.startswith("/reports/"):
            return httpx.Response(200, json={"items": [{"text": "r", "reporter": "p", "ts": "t"}]})
        # ---- olay akışı (veri seti)
        if key == "GET :8002/dataset/images":
            return httpx.Response(200, json={"dataset_date": "2025-06-01", "n_tracks": 1, "errors": [], "images": [IMG, {**IMG, "image_id": "img_000861", "capture_time": "14:15", "capture_iso": "2025-06-01T14:15:00Z", "center": {"lat": 39.92, "lon": 32.89}}]})
        if key == "GET :8002/dataset/images/img_000860":
            return httpx.Response(200, json=IMG)
        if key == "GET :8002/dataset/images/img_000861":
            return httpx.Response(200, json={**IMG, "image_id": "img_000861", "capture_time": "14:15", "capture_iso": "2025-06-01T14:15:00Z", "center": {"lat": 39.92, "lon": 32.89}})
        if path.startswith("/dataset/images/"):
            return httpx.Response(404, json={"detail": "Veri setinde image_id yok"})
        if key == "GET :8004/base":
            return httpx.Response(200, json=DS_BASE)
        if key == "GET :8004/zones":
            return httpx.Response(200, json=DS_ZONES)
        if key == "POST :8004/zones/assign":
            pts = json.loads(request.content)["points"]
            return httpx.Response(200, json={"assignments": [{"zone_id": "kuzey-yolu" if p["lat"] > 39.922 else "dogu-yolu", "name": "Kuzey Yolu" if p["lat"] > 39.922 else "Dogu Yolu", "distance_m": 100.0} for p in pts]})
        if key == "GET :8001/images/img-1":
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        return httpx.Response(404, json={"detail": f"beklenmeyen {key}"})


@pytest.fixture
def client_factory(monkeypatch):
    def make(world: World | None = None, keys: str = ""):
        monkeypatch.setenv("GATEWAY_API_KEYS", keys)
        # settings/auth import anında okunur -> modülü yeni env ile yeniden kur
        import importlib

        importlib.reload(gw)
        world = world or World()
        client = TestClient(gw.app)
        client.__enter__()
        gw._state["svc"].http = httpx.AsyncClient(transport=httpx.MockTransport(world))
        return client, world

    clients = []
    yield lambda *a, **k: (lambda c: (clients.append(c[0]), c)[1])(make(*a, **k))
    for c in clients:
        c.__exit__(None, None, None)


def test_pipeline_full_success(client_factory):
    c, w = client_factory()
    r = c.post("/pipeline/run", json={"drone_id": "DRN-03", "reset_demo": True})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "succeeded"
    assert [s["status"] for s in run["steps"]] == ["succeeded"] * 6
    res = run["result"]
    assert res["detection_id"] == "det-1" and res["assessment"]["risk_level"] == "HIGH"
    assert set(res["vehicles"]) == {"V-101", "V-102"} and len(res["vehicles"]["V-101"]["track"]) == 120
    assert res["detections"][0]["bbox"] == {"x1": 100.0, "y1": 100.0, "x2": 120.0, "y2": 150.0}
    assert res["pattern"]["pattern"] == "CONVOY"
    # sıra: reset -> drones -> detect -> georef -> tracks -> pattern(+sweep) -> assess
    order = [x for x in w.calls if not x.endswith("/health")]
    assert order[0] == "POST :8002/admin/demo/reset"
    assert order.index("GET :8004/drones") < order.index("POST :8001/detect") < order.index("POST :8002/georeference") < order.index("POST :8003/pattern/classify") < order.index("POST :8005/assess")
    # /api öneki de çalışır
    assert c.get("/api/pipeline/runs").json()["runs"][0]["risk_level"] == "HIGH"


def test_pipeline_async_and_polling_state(client_factory):
    c, _ = client_factory()
    r = c.post("/pipeline/run?wait=false", json={"drone_id": "DRN-03"})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    # TestClient aynı event loop'ta çalıştığı için görev bittikten sonra sorgula
    for _ in range(50):
        st = c.get(f"/pipeline/runs/{run_id}").json()
        if st["status"] in {"succeeded", "failed"}:
            break
    assert st["status"] == "succeeded"


def test_dashboard_state_aggregation_and_labels(client_factory):
    c, _ = client_factory()
    c.post("/pipeline/run", json={"drone_id": "DRN-03"})
    s = c.get("/dashboard/state").json()
    assert s["zone_id"] == "ZONE-ALPHA" and s["base"]["radius_m"] == 1500
    assert len(s["drones"]) == 2 and len(s["intel"]) == 1 and len(s["reports"]) == 1
    assert s["assessment"]["risk_level"] == "HIGH"
    by_id = {v["vehicle_id"]: v for v in s["vehicles"]}
    assert by_id["V-101"]["in_latest_detection"] is True and by_id["V-101"]["risk_level"] == "HIGH"
    assert by_id["V-101"]["pattern"] == "CONVOY"
    assert by_id["V-201"]["in_latest_detection"] is False and by_id["V-201"]["risk_level"] is None
    assert s["services"]["core"]["status"] == "up"
    assert any("Pipeline tamamlandı" in x["message"] for x in s["logs"])


def test_pipeline_failure_marks_step_and_skips_rest(client_factory):
    c, _ = client_factory(World(**{"POST :8001/detect": httpx.Response(500, json={"detail": "model çöktü"})}))
    r = c.post("/pipeline/run", json={"drone_id": "DRN-03"})
    assert r.status_code == 502
    run = r.json()
    assert run["status"] == "failed" and run["error"]["step"] == "detect"
    assert [s["status"] for s in run["steps"]] == ["succeeded", "failed", "skipped", "skipped", "skipped", "skipped"]


def test_offline_drone_rejected(client_factory):
    c, _ = client_factory()
    run = c.post("/pipeline/run", json={"drone_id": "DRN-08"}).json()
    assert run["status"] == "failed" and "çevrimdışı" in run["error"]["message"]


def test_no_detections_skips_assessment(client_factory):
    w = World(**{"POST :8002/georeference": httpx.Response(200, json={"detection_id": "det-9", "image_id": "img-1", "timestamp": "t", "detections": [], "skipped": []})})
    c, _ = client_factory(w)
    run = c.post("/pipeline/run", json={"drone_id": "DRN-03"}).json()
    assert run["status"] == "succeeded" and run["result"]["assessment"] is None
    assert "tespit edilmedi" in run["result"]["message"]
    assert [s["status"] for s in run["steps"]][3:] == ["skipped"] * 3


def test_dashboard_survives_partial_outage(client_factory):
    w = World()
    c, _ = client_factory(w)
    assert c.post("/pipeline/run", json={"drone_id": "DRN-03"}).status_code == 200
    # ardından reports kaynağı çöker: dashboard yine 200, hata panelde, diğer kaynaklar sağlam
    w.over["GET :8004/drones"] = httpx.Response(503, json={"detail": "down"})
    gw._state["gw"]._cache.clear()  # TTL önbelleğini boşalt: 'son-iyi-değer' yerine gerçek arızayı gör
    s = c.get("/dashboard/state")
    assert s.status_code == 200
    body = s.json()
    assert body["drones"] == [] and "drones" in body["errors"]
    assert len(body["intel"]) == 1  # diğer kaynaklar etkilenmedi


def test_auth_layer(client_factory):
    c, _ = client_factory(keys="k1,k2")
    assert c.get("/health").status_code == 200  # probe açık
    assert c.get("/dashboard/state").status_code == 401
    assert c.get("/api/dashboard/state", headers={"X-API-Key": "bad"}).status_code == 401
    assert c.get("/dashboard/state", headers={"X-API-Key": "k2"}).status_code == 200
    assert c.get("/api/dashboard/state", headers={"Authorization": "Bearer k1"}).status_code == 200
    assert c.post("/pipeline/run", json={"drone_id": "DRN-03"}).status_code == 401


def test_proxy_and_image(client_factory):
    c, _ = client_factory()
    assert c.get("/proxy/mock/intel/ZONE-ALPHA").json()["items"][0]["confidence"] == "low"
    assert c.get("/proxy/nope/x").status_code == 404
    assert c.post("/proxy/core/admin/demo/reset").status_code == 403
    assert c.get("/proxy/core/%2e%2e/mock/drones").status_code == 400
    img = c.get("/images/img-1")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


# ================================================================== OLAY (event snapshot) akışı
def test_event_pipeline_uses_capture_time_zone_and_dataset_base(client_factory):
    c, w = client_factory()
    r = c.post("/pipeline/run", json={"image_id": "img_000860"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["mode"] == "event" and run["status"] == "succeeded"
    assert [s["name"] for s in run["steps"]] == ["event_context", "detect", "georeference", "tracks", "vehicle_links", "pattern", "assess"]
    assert [s["status"] for s in run["steps"]] == ["succeeded"] * 7
    ev = run["result"]["event"]
    assert ev["capture_time"] == "14:10" and ev["reference_time"] == "2025-06-01T14:10:00Z"
    assert ev["zone"]["zone_id"] == "kuzey-yolu" and ev["base"]["lat"] == 39.92184  # üs zones.json'dan (env varsayılanı değil)
    assert run["request"]["zone_id"] == "kuzey-yolu"
    # detect: veri seti image_id + capture zamanı
    d = w.requests["POST :8001/detect"]["body"]
    assert d["image_id"] == "img_000860" and d["timestamp"] == "2025-06-01T14:10:00Z" and "drone_id" not in d
    # georef: köşe koordinatı yolu, iz eşleştirme, tracker'ı kirletme YOK
    g = w.requests["POST :8002/georeference"]["body"]
    assert g["match_tracks"] is True and g["ingest"] is False and g["reference_time"] == "2025-06-01T14:10:00Z" and "drone_meta" not in g
    # tracks: reference_time'a kadar kesilmiş iz, analiz reference_time + görüntüdeki güncel konum ile
    assert w.requests["GET :8002/tracks/V-102"]["params"]["until"] == "2025-06-01T14:10:00Z"
    ab = w.requests["POST :8002/tracks/analyze"]["body"]
    assert ab["reference_time"] == "2025-06-01T14:10:00Z" and ab["base_location"]["lat"] == 39.92184 and "current_position" in ab
    assert w.requests["POST :8003/pattern/classify"]["body"]["reference_time"] == "2025-06-01T14:10:00Z"
    assert w.requests["POST :8005/assess"]["body"]["base_location"]["radius_m"] == 1500
    # olay akışında drone kaydı ve bölge taraması yok
    assert "GET :8004/drones" not in w.calls and "GET :8002/vehicles" not in w.calls
    # sınıf: tracks.csv'de yok, tespit sınıfı kullanılır; desen araç kartına yazılır
    assert run["result"]["vehicles"]["V-101"]["class"] == "pickup" and run["result"]["vehicles"]["V-101"]["pattern"] == "CONVOY"


def test_cross_event_candidates_reach_dashboard_and_risk_context(client_factory):
    c, w = client_factory()
    first = c.post("/pipeline/run", json={"image_id": "img_000860"}).json()
    second = c.post("/pipeline/run", json={"image_id": "img_000861"}).json()

    assert first["result"]["candidate_vehicle_links"] == []
    assert len(first["result"]["vehicle_graph"]["nodes"]) == 2
    links = second["result"]["candidate_vehicle_links"]
    assert len(links) == 2
    assert all(link["source_event_id"] == "img_000860" for link in links)
    assert all(link["target_event_id"] == "img_000861" for link in links)
    assert all(link["relation"] == "POSSIBLE_SAME_VEHICLE" for link in links)
    assert len(second["result"]["vehicle_graph"]["edges"]) == 2

    state = c.get("/dashboard/state").json()
    assert state["latest_run"]["result"]["candidate_vehicle_links"] == links
    risk_body = w.requests["POST :8005/assess"]["body"]
    assert risk_body["vehicle_link_evidence"] == links


def test_appearance_outage_keeps_event_pipeline_successful(client_factory):
    world = World(**{"POST :8001/appearance/embed": httpx.Response(503, json={"detail": "weights unavailable"})})
    c, w = client_factory(world)
    run = c.post("/pipeline/run", json={"image_id": "img_000860"}).json()

    assert run["status"] == "succeeded"
    assert run["result"]["candidate_vehicle_links"] == []
    assert run["result"]["vehicle_graph"]["edges"] == []
    step = next(item for item in run["steps"] if item["name"] == "vehicle_links")
    assert step["status"] == "succeeded" and "kullanılamadı" in step["detail"]
    assert w.requests["POST :8005/assess"]["body"]["vehicle_link_evidence"] == []


def test_event_dashboard_state_is_event_scoped(client_factory):
    c, w = client_factory()
    c.post("/pipeline/run", json={"image_id": "img_000860"})
    s = c.get("/dashboard/state").json()
    assert s["mode"] == "event" and s["zone_id"] == "kuzey-yolu"
    assert s["base"]["lat"] == 39.92184 and s["base"]["radius_m"] == 1500
    assert s["event"]["capture_time"] == "14:10"
    assert s["drones"] == [] and {v["vehicle_id"] for v in s["vehicles"]} == {"V-101", "V-102"}
    v = next(v for v in s["vehicles"] if v["vehicle_id"] == "V-101")
    assert v["in_latest_detection"] and v["risk_level"] == "HIGH" and len(v["trace"]) == 120 and v["last_position"]["lat"] == 39.95
    assert s["assessment"]["risk_level"] == "HIGH"
    # saha raporları olay anına ve konumuna göre istendi
    p = w.requests["GET :8004/reports/kuzey-yolu"]["params"]
    assert p["as_of"] == "2025-06-01T14:10:00Z" and float(p["lat"]) == pytest.approx(39.9253)


def test_events_catalog_with_last_run(client_factory):
    c, _ = client_factory()
    ev = c.get("/events").json()
    assert [e["image_id"] for e in ev["events"]] == ["img_000860", "img_000861"]
    assert [e["zone_id"] for e in ev["events"]] == ["kuzey-yolu", "dogu-yolu"]  # bölge sırası zones.json'daki gibi
    assert ev["events"][0]["capture_time"] == "14:10" and ev["events"][0]["last_run"] is None
    c.post("/pipeline/run", json={"image_id": "img_000860"})
    ev = c.get("/events").json()["events"]
    assert ev[0]["last_run"]["risk_level"] == "HIGH" and ev[1]["last_run"] is None


def test_event_unknown_image_fails_at_context(client_factory):
    c, _ = client_factory()
    r = c.post("/pipeline/run", json={"image_id": "img_yok"})
    run = r.json()
    assert r.status_code == 502 and run["error"]["step"] == "event_context" and "image_id" in run["error"]["message"]
    assert [s["status"] for s in run["steps"]] == ["failed"] + ["skipped"] * 6


def test_event_without_track_match_skips_assessment(client_factory):
    geo = httpx.Response(200, json={"detection_id": "det-9", "image_id": "img-1", "timestamp": "t", "reference_time": "2025-06-01T14:10:00Z", "georef_method": "corners",
                                    "detections": [{"class": "car", "conf": 0.9, "lat": 39.9, "lon": 32.8, "vehicle_id": None, "box_index": 0}], "skipped": []})
    c, w = client_factory(World(**{"POST :8002/georeference": geo}))
    run = c.post("/pipeline/run", json={"image_id": "img_000860"}).json()
    assert run["status"] == "succeeded" and run["result"]["assessment"] is None
    assert "izle" in run["result"]["message"] and run["result"]["detections"][0]["vehicle_id"] is None
    assert "POST :8005/assess" not in w.calls


def test_inline_meta_needs_capture_time_and_request_validation(client_factory):
    c, _ = client_factory()
    assert c.post("/pipeline/run", json={}).status_code == 422
    meta = {"width_px": 100, "height_px": 50, "corner_coordinates": {"top_left": [1.001, 2.0], "top_right": [1.001, 2.001], "bottom_left": [1.0, 2.0], "bottom_right": [1.0, 2.001]}}
    bad = c.post("/pipeline/run", json={"image_meta": meta, "image_b64": "x"}).json()
    assert bad["status"] == "failed" and "capture_time" in bad["error"]["message"]
    ok = c.post("/pipeline/run", json={"image_meta": meta, "capture_time": "10:00", "image_b64": "x"}).json()
    assert ok["status"] == "succeeded" and ok["result"]["event"]["capture_time"] == "10:00"
