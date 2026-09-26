from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DetectRequest(BaseModel):
    image_b64: str | None = Field(None, description="Base64 (opsiyonel data: URL öneki ile) görüntü")
    image_url: str | None = Field(None, description="http(s) görüntü adresi")
    image_id: str | None = Field(None, description="Veri setindeki görüntü kimliği (DATA_DIR/images/<id>.jpg); b64/url yoksa buradan okunur")
    drone_id: str | None = Field(None, description="Eski/demo akışı için; olay (image_id) akışında gerekmez")
    timestamp: str | None = Field(None, description="ISO-8601 (olayda capture_time); verilmezse sunucu zamanı")


class Box(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cls: str = Field(..., alias="class")
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float


class DetectResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    image_id: str
    drone_id: str | None = None
    timestamp: str
    boxes: list[Box]
    image_width: int
    image_height: int
    mode: str = Field(..., description="'mock' | 'model'")
    backend: str = Field("mock", description="'mock' | 'ultralytics' | 'dfine'")
    fallback_reason: str | None = Field(None, description="Dolu ise model yüklenemedi ve mock'a düşüldü (degraded)")
    inference_ms: float
