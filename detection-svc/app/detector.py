"""Dedektörler: gerçek model (ultralytics YOLO | D-FINE) ve mock. Model yüklenemezse sessizce mock'a DÜŞMEZ."""
from __future__ import annotations

import hashlib
import logging
import os
import random
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from .config import Settings
from .dataset import GroundTruth

log = logging.getLogger("detection-svc.detector")

DEFAULT_W, DEFAULT_H = 1920, 1080


@dataclass
class Det:
    cls: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float


class ModelLoadError(RuntimeError):
    """MOCK_MODE=false iken model yüklenemedi (ve MOCK_FALLBACK=false). Servis açılmamalı."""


class Detector(Protocol):
    mode: str  # "mock" | "model"
    backend: str  # "mock" | "ultralytics" | "dfine"
    fallback_reason: str | None  # dolu ise: model istendi ama yüklenemedi, mock'a düşüldü (degraded)

    def detect(self, img: Image.Image | None, drone_id: str | None, seed_key: str, image_id: str | None = None) -> tuple[list[Det], int, int]:
        """(kutular, görüntü_genişliği, görüntü_yüksekliği)"""
        ...


# --------------------------------------------------------------------------------------
# DEMO SAHNESİ — DRN-03, nadir (gimbal -90°), irtifa 120 m, yaw 315°, hFOV 84°, 1920x1080.
# Kutu merkezleri (normalize) core-svc demo senaryosundaki kafilenin τ=0 konumlarına karşılık gelir:
#   V-101 (öncü pickup)  : ileri +45 m -> y≈0.130
#   V-102 (kamyon)       : tam merkez
#   V-103 (kapanış pick.) : geri  -45 m -> y≈0.870
# Bu sabitler README "Demo senaryosu" bölümünde açıklanan geometriden türetilmiştir.
# --------------------------------------------------------------------------------------
DEMO_SCENES: dict[str, list[tuple[str, float, float, float, float, float]]] = {
    "DRN-03": [
        # sınıf, conf, cx, cy, w, h  (hepsi görüntüye göre normalize)
        ("pickup", 0.93, 0.4885, 0.1298, 0.0093, 0.0436),
        ("truck", 0.95, 0.5000, 0.5000, 0.0116, 0.0700),
        ("pickup", 0.91, 0.4930, 0.8702, 0.0093, 0.0436),
    ],
}


class MockDetector:
    """Model yokken kullanılan deterministik sahte dedektör.

    Aynı (drone_id, görüntü) daima aynı kutuları üretir; böylece tracker sahte araçları
    her çalıştırmada yeniden yaratmaz.
    """

    mode = "mock"
    backend = "mock"

    def __init__(self, settings: Settings, ground_truth: GroundTruth | None = None, fallback_reason: str | None = None) -> None:
        self.fallback_reason = fallback_reason
        self._classes = settings.mock_classes or ["car"]
        self._gt = ground_truth

    def detect(self, img: Image.Image | None, drone_id: str | None, seed_key: str, image_id: str | None = None) -> tuple[list[Det], int, int]:
        w, h = (img.width, img.height) if img is not None else (DEFAULT_W, DEFAULT_H)
        # 1) Veri seti olayı: ground-truth bbox'lar (gerçek model gelene kadar mock'u bunlar besler)
        gt = self._gt.get(image_id) if (self._gt is not None and image_id) else None
        if gt is not None:
            gw, gh = int(gt.get("width_px") or w), int(gt.get("height_px") or h)
            sx, sy = w / gw, h / gh
            boxes = [
                Det(str(b["class"]), float(b.get("conf", 0.9)), round(b["x1"] * sx, 1), round(b["y1"] * sy, 1), round(b["x2"] * sx, 1), round(b["y2"] * sy, 1))
                for b in gt.get("boxes", [])
            ]
            return boxes, w, h
        # 2) Eski/demo akışı: drone demo sahnesi veya rastgele
        scene = DEMO_SCENES.get((drone_id or "").upper())
        if scene is not None:
            return [self._from_norm(*s, w, h) for s in scene], w, h

        seed = int(hashlib.sha1(f"{drone_id or image_id}|{seed_key}".encode()).hexdigest()[:12], 16)
        rng = random.Random(seed)
        boxes: list[Det] = []
        for _ in range(rng.randint(1, 3)):
            cls = rng.choice(self._classes)
            bw = rng.uniform(0.015, 0.05)
            bh = bw * rng.uniform(1.4, 2.6)
            cx = rng.uniform(0.15, 0.85)
            cy = rng.uniform(0.15, 0.85)
            boxes.append(self._from_norm(cls, round(rng.uniform(0.55, 0.97), 2), cx, cy, bw, bh, w, h))
        return boxes, w, h

    @staticmethod
    def _from_norm(cls: str, conf: float, cx: float, cy: float, bw: float, bh: float, w: int, h: int) -> Det:
        x1 = max(0.0, (cx - bw / 2) * w)
        y1 = max(0.0, (cy - bh / 2) * h)
        x2 = min(float(w), (cx + bw / 2) * w)
        y2 = min(float(h), (cy + bh / 2) * h)
        return Det(cls, conf, round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1))


class YoloDetector:
    """Ultralytics YOLO (Stage 1) sarmalayıcısı. `ultralytics` yalnızca burada, tembel import edilir."""

    mode = "model"
    backend = "ultralytics"
    fallback_reason: str | None = None

    def __init__(self, settings: Settings) -> None:
        if not os.path.isfile(settings.model_path):
            raise FileNotFoundError(f"Model dosyası yok: {settings.model_path}")
        from ultralytics import YOLO  # noqa: PLC0415  (ağır bağımlılık, opsiyonel)

        self._model = YOLO(settings.model_path)
        self._s = settings
        self._wanted = {c.lower() for c in settings.vehicle_classes}
        log.info("YOLO modeli yüklendi: %s (sınıflar: %s)", settings.model_path, self._model.names)

    def detect(self, img: Image.Image | None, drone_id: str | None, seed_key: str, image_id: str | None = None) -> tuple[list[Det], int, int]:
        if img is None:
            raise ValueError("Model modunda görüntü zorunludur")
        results = self._model.predict(
            img, conf=self._s.conf_threshold, iou=self._s.iou_threshold, imgsz=self._s.img_size, verbose=False
        )
        out: list[Det] = []
        for r in results:
            names = r.names
            for xyxy, conf, cls_idx in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
                name = str(names[int(cls_idx)])
                if self._wanted and name.lower() not in self._wanted:
                    continue
                x1, y1, x2, y2 = xyxy
                out.append(Det(name, round(float(conf), 4), round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)))
        return out, img.width, img.height


def _build_model(settings: Settings) -> Detector:
    if settings.model_backend == "dfine":
        from .dfine import DFineDetector  # noqa: PLC0415  (torch + resmî D-FINE reposu: yalnızca gerektiğinde)

        return DFineDetector(settings)
    if settings.model_backend == "ultralytics":
        try:
            return YoloDetector(settings)
        except AttributeError as exc:
            if "'dict' object has no attribute" in str(exc):
                raise ModelLoadError(
                    f"{settings.model_path} bir ultralytics modeli değil (ckpt['model'] bir state_dict). "
                    "D-FINE checkpoint'i ise MODEL_BACKEND=dfine kullanın."
                ) from exc
            raise
    raise ModelLoadError(f"Bilinmeyen MODEL_BACKEND={settings.model_backend!r} (ultralytics | dfine)")


def build_detector(settings: Settings) -> Detector:
    if settings.mock_mode:
        gt = GroundTruth(settings.data_dir)  # yalnızca mock kullanır; bozuksa DatasetError
        log.warning("MOCK_MODE=true: bbox'lar mock (veri seti ground_truth.json varsa onunla, yoksa rastgele/demo)")
        return MockDetector(settings, gt)
    try:
        return _build_model(settings)
    except Exception as exc:  # model yok / bağımlılık kurulu değil / bozuk ağırlık / uyumsuz backend
        reason = f"{type(exc).__name__}: {exc}"
        if not settings.mock_fallback:
            raise ModelLoadError(f"Model yüklenemedi ({reason}). Mock'a düşmek için açıkça MOCK_FALLBACK=true verin.") from exc
        log.error("Model yüklenemedi (%s) — MOCK_FALLBACK=true: mock devrede, /health 'degraded' raporlayacak", reason)
        return MockDetector(settings, GroundTruth(settings.data_dir), fallback_reason=reason)
