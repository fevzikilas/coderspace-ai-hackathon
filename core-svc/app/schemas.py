from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Box(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cls: str = Field(..., alias="class")
    conf: float = 1.0
    x1: float
    y1: float
    x2: float
    y2: float


class DroneMeta(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    alt: float = Field(..., gt=0, description="Yerden yükseklik (AGL), metre")
    heading: float = Field(..., description="Kuzeyden saat yönünde derece")
    gimbal_pitch: float = Field(-90.0, ge=-90, le=90, description="0=ufuk, -90=nadir")
    fov: float = Field(..., gt=1, lt=179, description="Yatay görüş açısı, derece")
    sensor_w: float = Field(..., gt=0, description="Görüntü genişliği, piksel")
    sensor_h: float = Field(..., gt=0, description="Görüntü yüksekliği, piksel")


LatLon = tuple[float, float]


class Corners(BaseModel):
    """Kuşbakışı görüntü köşeleri. Piksel→koordinat yalnızca top_left/top_right/bottom_left ile hesaplanır (resmî formül); bottom_right şemada bulunur ama hesaba girmez."""

    top_left: LatLon
    top_right: LatLon
    bottom_left: LatLon
    bottom_right: LatLon


class ImageMetaIn(BaseModel):
    width_px: int = Field(..., gt=0)
    height_px: int = Field(..., gt=0)
    capture_time: str | None = Field(None, description="'HH:MM' veya ISO-8601")
    corner_coordinates: Corners


class GeoRequest(BaseModel):
    """Georeferans girdisi — üç yoldan biri (öncelik sırasıyla):
    1. `image_meta` (köşe koordinatları, satır içi)  2. `drone_meta` (eski/demo yolu: kamera modeli)
    3. yalnızca `image_id` → veri setindeki (image_meta.json) köşe koordinatları.
    """

    image_id: str
    boxes: list[Box]
    drone_meta: DroneMeta | None = None
    image_meta: ImageMetaIn | None = None
    drone_id: str | None = None
    zone_id: str | None = None
    timestamp: str | float | None = Field(None, description="Eski ad: reference_time ile aynı")
    reference_time: str | float | None = Field(None, description="Değerlendirme anı (capture_time). Köşe yolunda yoksa image_meta'dan gelir")
    ingest: bool = Field(True, description="False: sadece koordinat hesapla, tracker'a yazma (olay değerlendirmesi)")
    match_tracks: bool = Field(False, description="True: tespitleri reference_time anındaki tracks.csv izlerine eşle (depoyu değiştirmez)")


class GeoDetection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cls: str = Field(..., alias="class")
    conf: float
    lat: float
    lon: float
    vehicle_id: str | None = None
    box_index: int
    match_distance_m: float | None = Field(None, description="match_tracks: tespit ile eşleşen iz noktası arası mesafe")
    match_method: str | None = Field(None, description="match_tracks: 'exact' (time == capture_time satırı) | 'interpolated' (ızgara dışı yedek yol) | None (iz bulunamadı)")


class GeoResponse(BaseModel):
    detection_id: str
    image_id: str
    timestamp: str
    reference_time: str | None = None
    capture_time: str | None = None
    georef_method: str = "drone_meta"
    zone_id: str | None = None
    detections: list[GeoDetection]
    skipped: list[dict[str, Any]] = []
    ingested: bool = True


class BaseLocation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    radius_m: float | None = Field(None, ge=0, description="Üs sınırı yarıçapı; yoksa servis varsayılanı")


class Coord(BaseModel):
    lat: float
    lon: float
    ts: str | float | None = None


class LatLonIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class AnalyzeRequest(BaseModel):
    vehicle_id: str | None = None
    coords: list[Coord] | None = Field(None, description="vehicle_id yerine ham iz (ts zorunlu, ≥2 nokta)")
    base_location: BaseLocation | None = None
    reference_time: str | float | None = Field(None, description="Değerlendirme anı; iz bu andan sonrası kesilir. Yoksa iz sonu")
    current_position: LatLonIn | None = Field(None, description="Görüntüden hesaplanan güncel konum; iz reference_time'a ulaşmıyorsa son nokta olarak eklenir")

    @model_validator(mode="after")
    def _one_source(self) -> "AnalyzeRequest":
        if not self.vehicle_id and not self.coords:
            raise ValueError("vehicle_id veya coords verilmeli")
        return self
