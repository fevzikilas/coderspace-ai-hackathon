"""Frozen crop appearance embeddings for cross-event candidate evidence.

This module deliberately exposes visual similarity evidence, not vehicle identity.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from PIL import Image, ImageFilter, ImageStat


class AppearanceUnavailable(RuntimeError):
    """The optional frozen embedding backbone could not be initialized."""


class MobileNetV3SmallBackbone:
    """Lazily loaded ImageNet-pretrained MobileNetV3-Small feature extractor."""

    model_name = "torchvision/mobilenet_v3_small-imagenet1k-v1"

    def __init__(self) -> None:
        self._model: Any | None = None
        self._preprocess: Any | None = None
        self._torch: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import torch  # noqa: PLC0415
                from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small  # noqa: PLC0415

                weights = MobileNet_V3_Small_Weights.DEFAULT
                model = mobilenet_v3_small(weights=weights)
                model.classifier = torch.nn.Identity()
                model.eval()
                for parameter in model.parameters():
                    parameter.requires_grad_(False)
            except Exception as exc:  # noqa: BLE001 - translated to a service diagnostic
                raise AppearanceUnavailable(f"MobileNetV3-Small embedding modeli yüklenemedi: {exc}") from exc
            self._torch = torch
            self._model = model
            self._preprocess = weights.transforms()

    def __call__(self, crop: Image.Image) -> list[float]:
        self._load()
        assert self._torch is not None and self._model is not None and self._preprocess is not None
        tensor = self._preprocess(crop).unsqueeze(0)
        with self._lock, self._torch.inference_mode():
            features = self._model(tensor).squeeze(0).cpu().tolist()
        return [float(value) for value in features]


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise AppearanceUnavailable("Embedding normu sıfır veya geçersiz")
    return [float(value) / norm for value in vector]


def _crop_box(
    image: Image.Image,
    bbox: dict[str, float],
    padding: float,
) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = (float(bbox[key]) for key in ("x1", "y1", "x2", "y2"))
    if x2 <= x1 or y2 <= y1:
        return None
    dx, dy = (x2 - x1) * padding, (y2 - y1) * padding
    left = max(0, int(math.floor(x1 - dx)))
    top = max(0, int(math.floor(y1 - dy)))
    right = min(image.width, int(math.ceil(x2 + dx)))
    bottom = min(image.height, int(math.ceil(y2 + dy)))
    return (left, top, right, bottom) if right > left and bottom > top else None


def _quality(crop: Image.Image, image: Image.Image) -> dict[str, float | int]:
    area_ratio = (crop.width * crop.height) / max(1, image.width * image.height)
    edges = crop.convert("L").filter(ImageFilter.FIND_EDGES)
    sharpness = min(1.0, float(ImageStat.Stat(edges).stddev[0]) / 64.0)
    size_score = min(1.0, math.sqrt(area_ratio / 0.04))
    return {
        "width_px": crop.width,
        "height_px": crop.height,
        "area_ratio": round(area_ratio, 6),
        "sharpness": round(sharpness, 4),
        "score": round(0.65 * size_score + 0.35 * sharpness, 4),
    }


class AppearanceExtractor:
    def __init__(
        self,
        *,
        backbone: Callable[[Image.Image], Sequence[float]] | None = None,
        cache_size: int = 256,
        crop_padding: float = 0.08,
        min_crop_pixels: int = 8,
    ) -> None:
        self._backbone = backbone or MobileNetV3SmallBackbone()
        self.model_name = getattr(self._backbone, "model_name", self._backbone.__class__.__name__)
        self._capacity = max(1, cache_size)
        self._padding = max(0.0, crop_padding)
        self._min_crop_pixels = max(1, min_crop_pixels)
        self._cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def cache_info(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._cache), "capacity": self._capacity, "hits": self._hits, "misses": self._misses}

    def embed(self, image_id: str, image: Image.Image, crops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in crops:
            box = _crop_box(image, item["bbox"], self._padding)
            if box is None:
                continue
            left, top, right, bottom = box
            if right - left < self._min_crop_pixels or bottom - top < self._min_crop_pixels:
                continue
            key = (image_id, left, top, right, bottom, self.model_name)
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._hits += 1
                    self._cache.move_to_end(key)
            if cached is None:
                crop = image.crop(box)
                payload = {
                    "embedding": _normalize(self._backbone(crop)),
                    "quality": _quality(crop, image),
                    "model": self.model_name,
                    "crop": {
                        "image_id": image_id,
                        "bbox": {"x1": left, "y1": top, "x2": right, "y2": bottom},
                    },
                }
                with self._lock:
                    self._misses += 1
                    self._cache[key] = payload
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._capacity:
                        self._cache.popitem(last=False)
                cached = payload
            results.append({"track_id": item["track_id"], **cached})
        return results
