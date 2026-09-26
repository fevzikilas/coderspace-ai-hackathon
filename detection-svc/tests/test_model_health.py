"""Model yükleme davranışı: sessiz mock yok, /health durumu açık; D-FINE saf fonksiyonları; (ortam varsa) gerçek ağırlık."""
import os

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.detector import ModelLoadError, build_detector
from app.dfine import size_from_anchor_count, to_detections


def _client(monkeypatch, **kw):
    monkeypatch.setattr(main, "settings", Settings(**kw))
    return TestClient(main.app)


def test_explicit_mock_is_ok_not_degraded(monkeypatch):
    with _client(monkeypatch, mock_mode=True) as c:
        h = c.get("/health").json()
        assert (h["status"], h["mode"], h["backend"], h["model_loaded"], h["fallback_reason"]) == ("ok", "mock", "mock", False, None)


def test_missing_model_fails_fast_by_default(monkeypatch):
    s = Settings(mock_mode=False, model_path="/nonexistent/model.pt")
    assert s.mock_fallback is False  # varsayılan: sessiz düşüş yok
    with pytest.raises(ModelLoadError) as ei:
        build_detector(s)
    assert "Model yüklenemedi" in str(ei.value) and "MOCK_FALLBACK=true" in str(ei.value)
    # servis de açılmaz
    monkeypatch.setattr(main, "settings", s)
    with pytest.raises(ModelLoadError):
        with TestClient(main.app):
            pass


def test_explicit_fallback_reports_degraded_with_reason(monkeypatch):
    with _client(monkeypatch, mock_mode=False, mock_fallback=True, model_path="/nonexistent/model.pt") as c:
        r = c.get("/health")
        assert r.status_code == 200  # konteyner sağlık kontrolü servisi öldürmesin
        h = r.json()
        assert h["status"] == "degraded" and h["mode"] == "mock" and h["model_loaded"] is False
        assert "Model dosyası yok" in h["fallback_reason"]
        d = c.post("/detect", json={"drone_id": "DRN-02"}).json()
        assert d["mode"] == "mock" and d["fallback_reason"] == h["fallback_reason"]


def test_unknown_backend_is_rejected():
    with pytest.raises(ModelLoadError, match="MODEL_BACKEND"):
        build_detector(Settings(mock_mode=False, model_backend="tensorrt"))


def test_dfine_checkpoint_given_to_ultralytics_gets_a_hint(monkeypatch, tmp_path):
    import app.detector as det

    f = tmp_path / "dfine.pt"
    f.write_bytes(b"x")

    class Boom:
        def __init__(self, s):
            raise AttributeError("'dict' object has no attribute 'to'")

    monkeypatch.setattr(det, "YoloDetector", Boom)
    with pytest.raises(ModelLoadError, match="MODEL_BACKEND=dfine"):
        build_detector(Settings(mock_mode=False, model_path=str(f), model_backend="ultralytics"))


def test_dfine_backend_without_repo_explains_what_is_missing(tmp_path):
    f = tmp_path / "m.pt"
    f.write_bytes(b"x")
    with pytest.raises(ModelLoadError, match="D-FINE yapılandırması yok"):
        build_detector(Settings(mock_mode=False, model_path=str(f), model_backend="dfine", dfine_repo=str(tmp_path / "nope")))


# ------------------------------------------------------------------------------------------ D-FINE saf fonksiyonlar
def test_size_from_anchor_count():
    assert size_from_anchor_count(33600) == 1280  # bizim checkpoint
    assert size_from_anchor_count(8400) == 640
    with pytest.raises(ModelLoadError):
        size_from_anchor_count(12345)


def _dets(**kw):
    base = dict(class_names=["car", "van", "truck", "bus"], conf_threshold=0.25, wanted=set(), width=100, height=80, max_detections=300)
    base.update(kw)
    return to_detections(**base)


def test_to_detections_filters_sorts_and_clips():
    labels = [1, 0, 2, 3, 9, 0]
    boxes = [[10, 10, 30, 30], [-5, 5, 20, 95], [50, 50, 50.4, 60], [60, 10, 90, 40], [1, 1, 5, 5], [0, 0, 10, 10]]
    scores = [0.9, 0.95, 0.8, 0.3, 0.7, 0.1]
    out = _dets(labels=labels, boxes=boxes, scores=scores)
    # skora göre: car(0.95, kırpılmış), van(0.9), bus(0.3). truck dejenere (genişlik<1), sınıf 9 geçersiz, 0.1 eşik altı
    assert [(d.cls, d.conf) for d in out] == [("car", 0.95), ("van", 0.9), ("bus", 0.3)]
    assert (out[0].x1, out[0].y1, out[0].x2, out[0].y2) == (0.0, 5.0, 20.0, 80.0)


def test_to_detections_class_filter_and_cap():
    labels, boxes, scores = [0, 1, 2], [[0, 0, 9, 9], [10, 10, 19, 19], [20, 20, 29, 29]], [0.9, 0.8, 0.7]
    assert [d.cls for d in _dets(labels=labels, boxes=boxes, scores=scores, wanted={"truck", "van"})] == ["van", "truck"]
    assert len(_dets(labels=labels, boxes=boxes, scores=scores, max_detections=2)) == 2


# ------------------------------------------------------------------------------------------ gerçek ağırlık (isteğe bağlı)
_REPO, _W = os.getenv("DFINE_REPO", ""), os.getenv("DFINE_TEST_WEIGHTS", "")


@pytest.mark.skipif(not (_REPO and _W and os.path.isfile(_W)), reason="DFINE_REPO ve DFINE_TEST_WEIGHTS ayarlı değil")
def test_real_dfine_weights_load_and_report_in_health(monkeypatch):
    pytest.importorskip("torch")
    s = Settings(mock_mode=False, model_backend="dfine", model_path=_W, dfine_repo=_REPO, class_names=["car", "van", "truck", "bus"])
    monkeypatch.setattr(main, "settings", s)
    with TestClient(main.app) as c:
        h = c.get("/health").json()
        assert (h["status"], h["mode"], h["backend"], h["model_loaded"], h["fallback_reason"]) == ("ok", "model", "dfine", True, None)
        # boş görüntü: hata vermeden (muhtemelen boş) liste döner; kutular görüntü sınırında
        import base64, io
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (640, 360), (60, 60, 60)).save(buf, format="PNG")
        r = c.post("/detect", json={"image_b64": base64.b64encode(buf.getvalue()).decode()})
        assert r.status_code == 200 and r.json()["backend"] == "dfine"
        assert all(0 <= b["x1"] < b["x2"] <= 640 and 0 <= b["y1"] < b["y2"] <= 360 for b in r.json()["boxes"])
        img = os.getenv("DFINE_TEST_IMAGE")
        if img:  # gerçek görüntü: en az bir araç bulunmalı
            b64 = base64.b64encode(open(img, "rb").read()).decode()
            boxes = c.post("/detect", json={"image_b64": b64}).json()["boxes"]
            assert len(boxes) >= 3 and {b["class"] for b in boxes} <= {"car", "van", "truck", "bus"}


def test_default_confidence_threshold_is_0_4_and_env_overridable(monkeypatch):
    monkeypatch.delenv("CONF_THRESHOLD", raising=False)
    assert Settings().conf_threshold == 0.4
    monkeypatch.setenv("CONF_THRESHOLD", "0.25")
    assert Settings().conf_threshold == 0.25
