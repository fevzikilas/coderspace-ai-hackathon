"""image_meta.json okuyucu — KATI (SADECE parse + doğrulama; iş mantığı yok).

Biçim (anahtar = image_id; başka alan/anahtar yasak):
  {"img_000860": {"width_px": 960, "height_px": 540, "capture_time": "14:10",
                  "corner_coordinates": {"top_left": [lat, lon], "top_right": [lat, lon],
                                          "bottom_left": [lat, lon], "bottom_right": [lat, lon]}}, ...}
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from ..geo import haversine_m
from ..timeutil import parse_ts
from .strict import DatasetError, StrictModel, parse_json_strict, validation_message

CORNER_KEYS = ("top_left", "top_right", "bottom_left", "bottom_right")
LatLon = tuple[float, float]
MIN_SIDE_M, MAX_SIDE_M = 5.0, 100_000.0  # görüntü ayak izi kenarı için makul aralık

ImageId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.\-]{1,80}$")]
ClockTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]  # "HH:MM"


def _check_latlon(v: list[float]) -> list[float]:
    lat, lon = v
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"koordinat aralık dışı ({lat}, {lon})")
    return v


Coord = Annotated[list[float], Field(min_length=2, max_length=2), AfterValidator(_check_latlon)]


class CornersModel(StrictModel):
    top_left: Coord
    top_right: Coord
    bottom_left: Coord
    bottom_right: Coord

    @model_validator(mode="after")
    def _geometry(self) -> "CornersModel":
        tl, tr, bl, br = (tuple(getattr(self, k)) for k in CORNER_KEYS)
        for name, a, b in (("üst kenar", tl, tr), ("alt kenar", bl, br), ("sol kenar", tl, bl), ("sağ kenar", tr, br)):
            d = haversine_m(a[0], a[1], b[0], b[1])
            if not MIN_SIDE_M <= d <= MAX_SIDE_M:
                raise ValueError(f"{name} uzunluğu {d:.1f} m (beklenen {MIN_SIDE_M:g}–{MAX_SIDE_M:g} m): köşeler dejenere/yanlış sırada")
        # alan sıfır olmamalı (köşeler doğrusal değil): yerel düzlemde çapraz çarpım
        ax, ay = tr[1] - tl[1], tr[0] - tl[0]
        bx, by = bl[1] - tl[1], bl[0] - tl[0]
        if abs(ax * by - ay * bx) < 1e-12:
            raise ValueError("köşeler doğrusal (alan sıfır)")
        return self


class ImageMetaModel(StrictModel):
    width_px: Annotated[int, Field(gt=0, le=100_000)]
    height_px: Annotated[int, Field(gt=0, le=100_000)]
    capture_time: ClockTime
    corner_coordinates: CornersModel


_FILE = TypeAdapter(dict[ImageId, ImageMetaModel])


@dataclass(frozen=True)
class Corners:
    top_left: LatLon
    top_right: LatLon
    bottom_left: LatLon
    bottom_right: LatLon

    @property
    def center(self) -> LatLon:
        """Görüntü merkezi = resmî formülün (W/2, H/2) noktası; `bottom_right` kullanılmaz."""
        return (self.top_left[0] + self.bottom_left[0]) / 2, (self.top_left[1] + self.top_right[1]) / 2


@dataclass(frozen=True)
class ImageMeta:
    image_id: str
    width_px: int
    height_px: int
    capture_time: str  # "HH:MM"
    capture_ts: float  # epoch (dataset_date üzerinde)
    corners: Corners


def parse_image_meta(text: str, base_date: str, source: str = "image_meta.json") -> dict[str, ImageMeta]:
    data = parse_json_strict(text, source)
    try:
        parsed = _FILE.validate_python(data)
    except ValidationError as exc:
        raise DatasetError(validation_message(source, exc)) from exc
    if not parsed:
        raise DatasetError(f"{source}: en az bir görüntü gerekli")
    out: dict[str, ImageMeta] = {}
    for image_id, m in parsed.items():
        c = m.corner_coordinates
        corners = Corners(*(tuple(getattr(c, k)) for k in CORNER_KEYS))  # type: ignore[arg-type]
        out[image_id] = ImageMeta(image_id, m.width_px, m.height_px, m.capture_time, parse_ts(m.capture_time, base_date=base_date), corners)
    return out


def load_image_meta(path: Path, base_date: str) -> dict[str, ImageMeta]:
    return parse_image_meta(path.read_text(encoding="utf-8"), base_date, path.name)


__all__ = ["Corners", "ImageMeta", "DatasetError", "parse_image_meta", "load_image_meta"]
