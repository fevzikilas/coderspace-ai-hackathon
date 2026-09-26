"""ground_truth.json: KATI doğrulama (mock dedektörü besler); bozuk dosya servisi açtırmaz."""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.dataset import parse_ground_truth
from app.strict import DatasetError

BOX = {"class": "car", "conf": 0.9, "x1": 10, "y1": 10, "x2": 30, "y2": 40}


def _gt(**box_over):
    return json.dumps({"img_1": {"width_px": 100, "height_px": 80, "boxes": [{**BOX, **box_over}]}})


def test_valid_gt_roundtrips_with_class_alias_and_optional_track():
    out = parse_ground_truth(_gt(track="T1"))
    assert out["img_1"]["boxes"][0]["class"] == "car" and out["img_1"]["boxes"][0]["track"] == "T1"
    assert parse_ground_truth(json.dumps({"img_1": {"width_px": 100, "height_px": 80, "boxes": []}}))["img_1"]["boxes"] == []


@pytest.mark.parametrize(
    "text, needle",
    [
        ("{", "geçersiz JSON"),
        ('{"img_1": {"width_px": 1, "height_px": 1, "boxes": []}, "img_1": {}}', "yinelenen anahtar"),
        ("[]", "dict"),
        (_gt(conf=1.5), "conf"),
        (_gt(x2=10), "dejenere"),  # x2 == x1
        (_gt(x1=-1), "x1"),
        (_gt(x2=101), "dışına taşıyor"),
        (_gt(**{"class": ""}), "class"),
        (_gt(extra=1), "extra"),
        (json.dumps({"../x": {"width_px": 1, "height_px": 1, "boxes": []}}), "../x"),
        (json.dumps({"img_1": {"width_px": "100", "height_px": 80, "boxes": []}}), "width_px"),
    ],
)
def test_gt_rejections(text, needle):
    with pytest.raises(DatasetError, match=needle):
        parse_ground_truth(text)


def test_broken_gt_stops_the_mock_service(tmp_path, monkeypatch):
    (tmp_path / "ground_truth.json").write_text(_gt(conf=2))
    monkeypatch.setattr(main, "settings", Settings(mock_mode=True, data_dir=str(tmp_path)))
    with pytest.raises(DatasetError, match="ground_truth.json"):
        with TestClient(main.app):
            pass
