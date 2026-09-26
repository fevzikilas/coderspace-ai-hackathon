"""Olay veri seti erişimi: görüntü dosyaları ve (mock için) ground-truth bbox'lar. Yalnızca okuma/parse."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from .strict import DatasetError, StrictModel, parse_json_strict, validation_message

log = logging.getLogger("detection-svc.dataset")

_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def safe_id(image_id: str) -> bool:
    """Yol gezinmesini (../, /) engelleyen kimlik denetimi."""
    return bool(_ID_RE.match(image_id)) and ".." not in image_id


def find_image(data_dir: str, image_id: str) -> Path | None:
    if not data_dir or not safe_id(image_id):
        return None
    root = Path(data_dir) / "images"
    for ext in _EXTS:
        p = root / f"{image_id}{ext}"
        if p.is_file():
            return p
    return None


def media_type(path: Path) -> str:
    return _MEDIA.get(path.suffix.lower(), "application/octet-stream")


class GtBox(StrictModel):
    cls: Annotated[str, Field(alias="class", min_length=1, max_length=40)]
    conf: Annotated[float, Field(ge=0, le=1)]
    x1: Annotated[float, Field(ge=0)]
    y1: Annotated[float, Field(ge=0)]
    x2: float
    y2: float
    track: Annotated[str, Field(min_length=1, max_length=40)] | None = None  # yalnızca araç/eval için etiket; servis kullanmaz

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _ordered(self) -> "GtBox":
        if not (self.x2 > self.x1 and self.y2 > self.y1):
            raise ValueError(f"kutu dejenere: ({self.x1}, {self.y1}, {self.x2}, {self.y2})")
        return self


class GtImage(StrictModel):
    width_px: Annotated[int, Field(gt=0, le=100_000)]
    height_px: Annotated[int, Field(gt=0, le=100_000)]
    boxes: list[GtBox]

    @model_validator(mode="after")
    def _inside(self) -> "GtImage":
        for i, b in enumerate(self.boxes):
            if b.x2 > self.width_px or b.y2 > self.height_px:
                raise ValueError(f"boxes[{i}] görüntü ({self.width_px}x{self.height_px}) dışına taşıyor: x2={b.x2}, y2={b.y2}")
        return self


_GT_FILE = TypeAdapter(dict[Annotated[str, StringConstraints(pattern=_ID_RE.pattern)], GtImage])


def parse_ground_truth(text: str, source: str = "ground_truth.json") -> dict[str, dict[str, Any]]:
    """KATI: {"img_000860": {"width_px": 960, "height_px": 540, "boxes": [{"class","conf","x1","y1","x2","y2"[,"track"]}]}}"""
    data = parse_json_strict(text, source)
    try:
        parsed = _GT_FILE.validate_python(data)
    except ValidationError as exc:
        raise DatasetError(validation_message(source, exc)) from exc
    return {k: v.model_dump(by_alias=True, exclude_none=True) for k, v in parsed.items()}


class GroundTruth:
    """DATA_DIR/ground_truth.json (isteğe bağlı): gerçek model gelene kadar MOCK dedektörü bu kutularla beslenir.

    Dosya varsa KATI doğrulanır ve açılışta okunur; bozuksa `DatasetError` (servis açılmaz)."""

    def __init__(self, data_dir: str) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        path = Path(data_dir) / "ground_truth.json" if data_dir else None
        if path is not None and path.is_file():
            self._data = parse_ground_truth(path.read_text(encoding="utf-8"), path.name)
            log.info("ground_truth.json yüklendi: %d görüntü", len(self._data))

    def get(self, image_id: str) -> dict[str, Any] | None:
        return self._data.get(image_id)
