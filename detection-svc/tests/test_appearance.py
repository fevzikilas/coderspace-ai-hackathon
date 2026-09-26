from __future__ import annotations

import importlib
import json
import math

from fastapi.testclient import TestClient
from PIL import Image


class CountingBackbone:
    model_name = "test-backbone"

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, crop: Image.Image) -> list[float]:
        self.calls += 1
        return [3.0, 4.0, float(crop.width + crop.height)]


def test_extractor_clips_crop_normalizes_embedding_and_reports_quality():
    from app.appearance import AppearanceExtractor

    backbone = CountingBackbone()
    extractor = AppearanceExtractor(backbone=backbone, cache_size=4, crop_padding=0.0, min_crop_pixels=4)
    image = Image.new("RGB", (40, 30), (40, 80, 120))

    result = extractor.embed(
        "img-1",
        image,
        [{"track_id": "T1", "bbox": {"x1": -2, "y1": 5, "x2": 12, "y2": 25}}],
    )[0]

    assert result["track_id"] == "T1"
    assert result["model"] == "test-backbone"
    assert result["crop"]["bbox"] == {"x1": 0, "y1": 5, "x2": 12, "y2": 25}
    assert result["quality"]["width_px"] == 12
    assert result["quality"]["height_px"] == 20
    assert 0.0 <= result["quality"]["score"] <= 1.0
    assert math.sqrt(sum(x * x for x in result["embedding"])) == 1.0


def test_extractor_reuses_cached_embedding_for_same_image_and_box():
    from app.appearance import AppearanceExtractor

    backbone = CountingBackbone()
    extractor = AppearanceExtractor(backbone=backbone, cache_size=2, crop_padding=0.0, min_crop_pixels=4)
    image = Image.new("RGB", (30, 30), "navy")
    crops = [{"track_id": "T1", "bbox": {"x1": 2, "y1": 3, "x2": 20, "y2": 22}}]

    first = extractor.embed("img-1", image, crops)
    second = extractor.embed("img-1", image, crops)

    assert first == second
    assert backbone.calls == 1
    assert extractor.cache_info() == {"size": 1, "capacity": 2, "hits": 1, "misses": 1}


def test_extractor_skips_tiny_or_degenerate_crops():
    from app.appearance import AppearanceExtractor

    extractor = AppearanceExtractor(backbone=CountingBackbone(), min_crop_pixels=8)
    image = Image.new("RGB", (30, 30), "black")
    result = extractor.embed(
        "img-1",
        image,
        [
            {"track_id": "tiny", "bbox": {"x1": 1, "y1": 1, "x2": 4, "y2": 4}},
            {"track_id": "bad", "bbox": {"x1": 12, "y1": 2, "x2": 8, "y2": 9}},
        ],
    )

    assert result == []


def test_appearance_endpoint_returns_track_embeddings(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "images").mkdir(parents=True)
    Image.new("RGB", (64, 48), (90, 40, 20)).save(data / "images" / "img_1.jpg")
    (data / "ground_truth.json").write_text(json.dumps({"img_1": {"width_px": 64, "height_px": 48, "boxes": []}}))
    monkeypatch.setenv("DATA_DIR", str(data))

    import app.main as main
    from app.appearance import AppearanceExtractor

    importlib.reload(main)
    with TestClient(main.app) as client:
        main._state["appearance"] = AppearanceExtractor(
            backbone=CountingBackbone(), crop_padding=0.0, min_crop_pixels=4
        )
        response = client.post(
            "/appearance/embed",
            json={
                "image_id": "img_1",
                "crops": [{"track_id": "T17", "bbox": {"x1": 4, "y1": 5, "x2": 30, "y2": 35}}],
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["image_id"] == "img_1"
    assert body["model"] == "test-backbone"
    assert len(body["tracks"]) == 1
    assert body["tracks"][0]["track_id"] == "T17"
    assert body["tracks"][0]["crop"]["image_id"] == "img_1"
