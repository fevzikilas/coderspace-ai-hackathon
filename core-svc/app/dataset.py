"""Olay veri seti (image_meta.json + tracks.csv) — yükleme ve kataloğ.

İş mantığı (georef, analiz) buraya girmez; yalnızca loader çıktılarını bellekte tutar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .geo import haversine_m
from .loaders.image_meta import DatasetError, ImageMeta, load_image_meta
from .loaders.tracks_csv import Point, load_tracks
from .timeutil import iso

log = logging.getLogger("core-svc.dataset")


@dataclass
class Dataset:
    images: dict[str, ImageMeta] = field(default_factory=dict)
    tracks: dict[str, list[Point]] = field(default_factory=dict)
    data_dir: str = ""

    def image_summary(self, meta: ImageMeta) -> dict:
        c = meta.corners
        center = c.center
        width_m = haversine_m(*c.top_left, *c.top_right)
        height_m = haversine_m(*c.top_left, *c.bottom_left)
        return {
            "image_id": meta.image_id,
            "capture_time": meta.capture_time,
            "capture_iso": iso(meta.capture_ts),
            "width_px": meta.width_px,
            "height_px": meta.height_px,
            "center": {"lat": round(center[0], 6), "lon": round(center[1], 6)},
            "footprint_m": {"width": round(width_m, 1), "height": round(height_m, 1)},
            "corner_coordinates": {
                "top_left": list(c.top_left), "top_right": list(c.top_right),
                "bottom_left": list(c.bottom_left), "bottom_right": list(c.bottom_right),
            },
        }


def load_dataset(settings: Settings) -> Dataset:
    """DATA_DIR boşsa boş veri seti döner (eski/demo akışı için servis yine çalışır).

    DATA_DIR verilmişse KATI davranılır: klasör, `image_meta.json` ve `tracks.csv` var olmalı ve geçerli olmalı; aksi halde
    `DatasetError` fırlatılır (servis açılmaz). Bozuk veri sessizce atlanmaz/onarılmaz."""
    ds = Dataset(data_dir=settings.data_dir)
    if not settings.data_dir:
        return ds
    root = Path(settings.data_dir)
    if not root.is_dir():
        raise DatasetError(f"DATA_DIR bulunamadı: {root}")
    for name in ("image_meta.json", "tracks.csv"):
        if not (root / name).is_file():
            raise DatasetError(f"{root / name} yok (DATA_DIR verildiğinde zorunlu)")
    ds.images = load_image_meta(root / "image_meta.json", settings.dataset_date)
    ds.tracks = load_tracks(root / "tracks.csv", settings.dataset_date)
    log.info("Veri seti yüklendi: %d görüntü, %d iz (%s)", len(ds.images), len(ds.tracks), root)
    return ds
