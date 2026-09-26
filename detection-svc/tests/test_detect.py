import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


def _png_b64(w=640, h=360) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 40)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_mock_detect_without_image():
    with TestClient(app) as c:
        r = c.post("/detect", json={"drone_id": "DRN-01", "timestamp": "2026-01-01T00:00:00Z"})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "mock"
        assert body["image_id"].startswith("img-")
        assert 1 <= len(body["boxes"]) <= 3
        for b in body["boxes"]:
            assert set(b) == {"class", "conf", "x1", "y1", "x2", "y2"}
            assert b["x2"] > b["x1"] and b["y2"] > b["y1"]


def test_mock_is_deterministic_per_drone():
    with TestClient(app) as c:
        a = c.post("/detect", json={"drone_id": "DRN-02"}).json()["boxes"]
        b = c.post("/detect", json={"drone_id": "DRN-02"}).json()["boxes"]
        assert a == b


def test_demo_scene_has_three_vehicles_for_drn03():
    with TestClient(app) as c:
        boxes = c.post("/detect", json={"drone_id": "DRN-03"}).json()["boxes"]
        assert [b["class"] for b in boxes] == ["pickup", "truck", "pickup"]


def test_image_scales_boxes_and_is_cached():
    with TestClient(app) as c:
        r = c.post("/detect", json={"drone_id": "DRN-03", "image_b64": _png_b64()})
        body = r.json()
        assert (body["image_width"], body["image_height"]) == (640, 360)
        assert all(b["x2"] <= 640 and b["y2"] <= 360 for b in body["boxes"])
        img = c.get(f"/images/{body['image_id']}")
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"


def test_bad_base64_is_400():
    with TestClient(app) as c:
        r = c.post("/detect", json={"drone_id": "DRN-01", "image_b64": "bm90IGFuIGltYWdl"})
        assert r.status_code == 400


def test_private_url_blocked():
    with TestClient(app) as c:
        r = c.post("/detect", json={"drone_id": "DRN-01", "image_url": "http://127.0.0.1:1/x.png"})
        assert r.status_code == 400


# ------------------------------------------------------------------ olay veri seti (image_id + ground truth)
import json  # noqa: E402
import os  # noqa: E402


def _dataset(tmp_path):
    (tmp_path / "images").mkdir()
    Image.new("RGB", (960, 540), (60, 90, 50)).save(tmp_path / "images" / "img_000001.jpg", "JPEG")
    (tmp_path / "ground_truth.json").write_text(json.dumps({
        "img_000001": {"width_px": 960, "height_px": 540, "boxes": [
            {"class": "pickup", "conf": 0.94, "x1": 470, "y1": 250, "x2": 490, "y2": 290},
            {"class": "car", "conf": 0.88, "x1": 100, "y1": 80, "x2": 130, "y2": 110}]}}))
    return tmp_path


def _app_with_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(_dataset(tmp_path)))
    import importlib

    import app.main as m

    importlib.reload(m)
    return m.app


def test_dataset_image_uses_ground_truth_boxes_and_serves_file(tmp_path, monkeypatch):
    app_ = _app_with_data(tmp_path, monkeypatch)
    with TestClient(app_) as c:
        r = c.post("/detect", json={"image_id": "img_000001", "timestamp": "2025-06-01T14:10:00Z"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["image_id"] == "img_000001" and body["drone_id"] is None
        assert (body["image_width"], body["image_height"]) == (960, 540)
        assert [(b["class"], b["x1"], b["y2"]) for b in body["boxes"]] == [("pickup", 470.0, 290.0), ("car", 100.0, 110.0)]
        img = c.get("/images/img_000001")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_uploaded_image_with_dataset_id_scales_ground_truth(tmp_path, monkeypatch):
    app_ = _app_with_data(tmp_path, monkeypatch)
    with TestClient(app_) as c:
        r = c.post("/detect", json={"image_id": "img_000001", "image_b64": _png_b64(480, 270)}).json()
        assert (r["image_width"], r["image_height"]) == (480, 270)
        assert r["boxes"][0]["x1"] == 235.0  # 470 * 0.5
        assert c.get("/images/img_000001").headers["content-type"] == "image/png"  # yüklenen baytlar önce gelir


def test_unknown_or_unsafe_image_id(tmp_path, monkeypatch):
    app_ = _app_with_data(tmp_path, monkeypatch)
    with TestClient(app_) as c:
        assert c.get("/images/yok").status_code == 404
        assert c.get("/images/..%2Fground_truth.json").status_code in (404, 422)
        assert c.post("/detect", json={"image_id": "../x"}).status_code == 400
        # veri setinde olmayan id ve görüntü yok: mock modda rastgele/boş değil, id yansıtılır
        assert c.post("/detect", json={"image_id": "img_999999"}).status_code == 200
    os.environ.pop("DATA_DIR", None)
