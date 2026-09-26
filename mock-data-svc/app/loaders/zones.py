"""zones.json okuyucu — KATI (SADECE parse + doğrulama).

Biçim (başka alan yasak):
  {"base":  {"name": "Merkez Us", "lat": 39.92184, "lon": 32.85306 [, "radius_m": 1500, "alert_radius_m": 8000]},
   "zones": [{"name": "Kuzey Yolu", "center": [39.950586, 32.853060]}, ...]}
`zone_id` dosyada yoktur; addan türetilir (ASCII'ye katlanmış slug: "Kuzey Yolu" -> "kuzey-yolu"). Aynı slug'a düşen iki bölge adı REDDEDİLİR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints, TypeAdapter, ValidationError

from ..geo import bearing_deg, haversine_m
from .strict import DatasetError, StrictModel, parse_json_strict, validation_message


@dataclass(frozen=True)
class BaseInfo:
    name: str
    lat: float
    lon: float
    radius_m: float | None
    alert_radius_m: float | None


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    lat: float
    lon: float
    distance_from_base_m: float
    bearing_from_base_deg: float


_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def fold(text: str) -> str:
    """Küçük harf + Türkçe karakterleri ASCII'ye katla (dosyalardaki metinler genelde ASCII'dir)."""
    return text.translate(_FOLD).lower()


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")
    return s or "zone"


def _check_latlon(v: list[float]) -> list[float]:
    lat, lon = v
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"koordinat aralık dışı ({lat}, {lon})")
    return v


Name = Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=False, pattern=r"^\S(.*\S)?$")]
Coord = Annotated[list[float], Field(min_length=2, max_length=2), AfterValidator(_check_latlon)]
Lat = Annotated[float, Field(ge=-90, le=90)]
Lon = Annotated[float, Field(ge=-180, le=180)]
Radius = Annotated[float, Field(gt=0, le=1_000_000)]


class BaseModel_(StrictModel):
    name: Name
    lat: Lat
    lon: Lon
    radius_m: Radius | None = None
    alert_radius_m: Radius | None = None


class ZoneModel(StrictModel):
    name: Name
    center: Coord


class ZonesFile(StrictModel):
    base: BaseModel_
    zones: Annotated[list[ZoneModel], Field(min_length=1)]


_FILE = TypeAdapter(ZonesFile)


def parse_zones(text: str, source: str = "zones.json") -> tuple[BaseInfo, list[Zone]]:
    data = parse_json_strict(text, source)
    try:
        f = _FILE.validate_python(data)
    except ValidationError as exc:
        raise DatasetError(validation_message(source, exc)) from exc
    b = f.base
    base = BaseInfo(b.name, b.lat, b.lon, b.radius_m, b.alert_radius_m)
    zones: list[Zone] = []
    seen: dict[str, str] = {}
    for z in f.zones:
        zid = slugify(z.name)
        if zid in seen:
            raise DatasetError(f"{source}: bölge adları aynı zone_id'ye düşüyor: {seen[zid]!r} ve {z.name!r} → {zid!r}")
        seen[zid] = z.name
        lat, lon = z.center
        zones.append(Zone(zid, z.name, lat, lon, round(haversine_m(base.lat, base.lon, lat, lon), 1), round(bearing_deg(base.lat, base.lon, lat, lon), 1)))
    return base, zones


def load_zones(path: Path) -> tuple[BaseInfo, list[Zone]]:
    return parse_zones(path.read_text(encoding="utf-8"), path.name)
