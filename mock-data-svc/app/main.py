"""mock-data-svc — bölge/üs, saha raporu, istihbarat ve drone kaydı.

Veri kaynağı:
  • DATA_DIR/zones.json + field_reports.json (olay veri seti; loader katmanı `app/loaders/`)
  • yoksa/ek olarak eski demo verisi (`app/data.py`: ZONE-ALPHA…, DRN-01…08; istihbarat YER TUTUCUDUR)
Saat mantığı `as_of` (= olayın capture_time'ı) üzerindendir; duvar saati yalnızca `as_of` verilmeyen ESKİ demo yolunda kullanılır.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import data
from .config import Settings, get_settings
from .dataset import Catalog
from .timeutil import iso, parse_ts

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("mock-data-svc")


class Point(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class AssignRequest(BaseModel):
    points: list[Point] = Field(..., min_length=1, max_length=500)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    holder: dict[str, Catalog] = {"catalog": Catalog(cfg)}

    def as_of_ts(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return parse_ts(value, cfg.dataset_date)
        except ValueError as exc:
            raise HTTPException(422, f"Geçersiz as_of: {exc}") from exc

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        holder["catalog"] = Catalog.load(cfg)
        yield

    app = FastAPI(title="mock-data-svc", version="2.0.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        c = holder["catalog"]
        return {
            "status": "ok",
            "service": "mock-data-svc",
            "dataset": {"base": c.base.name if c.base else None, "zones": len(c.zones), "field_reports": len(c.reports)},
        }

    # ------------------------------------------------------------------ üs + bölgeler
    @app.get("/base")
    def base() -> dict:
        """Korunan üs. Veri setinde varsa zones.json'daki, yoksa eski demo üssü."""
        c = holder["catalog"]
        d = c.base_dict()
        if d is not None:
            return d
        return {**data.BASE, "source": "legacy"}

    @app.get("/zones")
    def zones() -> dict:
        c = holder["catalog"]
        if c.zones:
            b = c.base_dict()
            return {"source": "dataset", "base": b, "zones": [c.zone_dict(z, b) for z in c.zones]}
        return {"source": "legacy", "zones": data.list_zones()}

    @app.post("/zones/assign")
    def assign(req: AssignRequest) -> dict:
        """Her nokta için en yakın bölge (merkeze göre) — olay görüntüsünün hangi bölgede olduğunu çözer."""
        c = holder["catalog"]
        if not c.zones:
            raise HTTPException(409, "Veri setinde bölge (zones.json) yok")
        out = []
        for p in req.points:
            near = c.nearest_zone(p.lat, p.lon)
            assert near is not None
            z, dist = near
            out.append({"zone_id": z.zone_id, "name": z.name, "distance_m": round(dist, 1)})
        return {"assignments": out}

    @app.get("/zones/{zone_id}")
    def zone(zone_id: str) -> dict:
        c = holder["catalog"]
        z = c.zone(zone_id)
        if z is not None:
            return c.zone_dict(z, c.base_dict())
        legacy = data.get_zone(zone_id)
        if legacy is None:
            raise HTTPException(404, f"Bilinmeyen zone: {zone_id}")
        return legacy

    # ------------------------------------------------------------------ istihbarat / saha raporu
    @app.get("/intel/{zone_id}")
    def intel(zone_id: str, as_of: str | None = Query(None, description="Değerlendirme anı (ISO | 'HH:MM')")) -> dict:
        """İstihbarat (güven daima 'low'). Veri setinde istihbarat kaynağı YOK: dataset bölgeleri için boş liste (yer tutucu)."""
        c = holder["catalog"]
        ts = as_of_ts(as_of)
        z = c.zone(zone_id)
        if z is not None:
            return {"zone_id": z.zone_id, "items": [], "placeholder": True, "note": "Veri setinde istihbarat kaynağı yok (yer tutucu)"}
        now = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None
        items = data.get_intel(zone_id, now=now)
        if items is None:
            raise HTTPException(404, f"Bilinmeyen zone: {zone_id}")
        return {"zone_id": zone_id.upper(), "items": items}

    @app.get("/reports/{zone_id}")
    def reports(
        zone_id: str,
        as_of: str | None = Query(None, description="Değerlendirme anı (ISO | 'HH:MM'); sonrasındaki raporlar döndürülmez"),
        lat: float | None = Query(None, ge=-90, le=90),
        lon: float | None = Query(None, ge=-180, le=180),
        radius_km: float | None = Query(None, gt=0, le=200),
    ) -> dict:
        """Saha raporları. Dataset bölgesi: `as_of`'a kadar yazılmış ve bu bölgeyle ilgili (koordinatı bölgeye düşen, bölge yolunu anan
        veya genel) raporlar; ayrıntı için `Catalog.reports_for`."""
        c = holder["catalog"]
        ts = as_of_ts(as_of)
        z = c.zone(zone_id)
        if z is not None:
            center = (lat, lon) if lat is not None and lon is not None else (z.lat, z.lon)
            items = c.reports_for(z.zone_id, center, ts, radius_km or cfg.report_radius_km)
            return {"zone_id": z.zone_id, "as_of": iso(ts) if ts is not None else None, "source": "dataset", "items": items}
        now = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None
        items = data.get_reports(zone_id, now=now)
        if items is None:
            raise HTTPException(404, f"Bilinmeyen zone: {zone_id}")
        return {"zone_id": zone_id.upper(), "as_of": iso(ts) if ts is not None else None, "source": "legacy", "items": items}

    # ------------------------------------------------------------------ drone kaydı (yalnızca eski/demo akışı)
    @app.get("/drones")
    def drones(animate: bool = Query(True, description="ORBIT drone'ları zamanla hareket ettir")) -> dict:
        return {"drones": data.list_drones(time.time(), animate=animate)}

    @app.get("/drones/{drone_id}")
    def drone(drone_id: str) -> dict:
        for d in data.list_drones(time.time()):
            if d["id"].upper() == drone_id.upper():
                return d
        raise HTTPException(404, f"Bilinmeyen drone: {drone_id}")

    return app


app = create_app()
