"""pattern-svc — kural tabanlı davranış sınıflandırma (LOITERING / DIRECT_APPROACH / CONVOY / RANDOM)."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .classifier import Base, Track, classify
from .config import get_settings

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pattern-svc")

settings = get_settings()
_http: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _http
    _http = httpx.AsyncClient(base_url=settings.core_svc_url, timeout=settings.http_timeout_s)
    yield
    await _http.aclose()


app = FastAPI(title="pattern-svc", version="1.0.0", lifespan=lifespan)


class BaseLocation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    radius_m: float | None = Field(None, ge=0)


class ClassifyRequest(BaseModel):
    vehicle_ids: list[str] = Field(..., min_length=1)
    zone_id: str
    base_location: BaseLocation | None = Field(None, description="Yoksa servis varsayılan üssü kullanılır")
    window: str = Field("2h", description="Tracks penceresi (core-svc formatı)")
    reference_time: str | None = Field(None, description="Değerlendirme anı (ISO | 'HH:MM'); izler bu ana kadar kesilir. Yoksa iz sonu")


def _parse_iso(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


async def _fetch_track(vehicle_id: str, window: str, until: str | None) -> tuple[str, Track | None]:
    assert _http is not None
    params = {"window": window, **({"until": until} if until else {})}
    try:
        r = await _http.get(f"/tracks/{vehicle_id}", params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"core-svc erişilemiyor: {exc}") from exc
    if r.status_code == 404:
        return vehicle_id, None
    if r.status_code != 200:
        raise HTTPException(502, f"core-svc /tracks/{vehicle_id} -> {r.status_code}: {r.text[:200]}")
    pts = r.json()["points"]
    return vehicle_id, sorted((_parse_iso(p["ts"]), p["lat"], p["lon"]) for p in pts)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "pattern-svc", "core_svc": settings.core_svc_url}


@app.post("/pattern/classify")
async def classify_pattern(req: ClassifyRequest) -> dict[str, Any]:
    ids = list(dict.fromkeys(req.vehicle_ids))  # tekrarsız, sıralı
    if len(ids) > settings.max_vehicles:
        raise HTTPException(422, f"En fazla {settings.max_vehicles} araç")

    fetched = await asyncio.gather(*(_fetch_track(v, req.window, req.reference_time) for v in ids))
    tracks: dict[str, Track] = {vid: t for vid, t in fetched if t}
    missing = [vid for vid, t in fetched if t is None]
    if not tracks:
        raise HTTPException(404, f"Hiçbir araç için iz bulunamadı: {missing}")

    bl = req.base_location
    base = Base(
        bl.lat if bl else settings.base_lat,
        bl.lon if bl else settings.base_lon,
        (bl.radius_m if bl and bl.radius_m is not None else settings.base_radius_m),
    )
    result = classify(tracks, base, settings)
    result["zone_id"] = req.zone_id
    result["reference_time"] = req.reference_time
    result["detail"]["missing_vehicles"] = missing
    log.info("classify zone=%s vehicles=%s -> %s (%.2f)", req.zone_id, ids, result["pattern"], result["confidence"])
    return result
