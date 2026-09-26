"""core-svc — georeferans (piksel→GPS) + tracking (hareket analizi).

İKİ çalışma biçimi:
  • OLAY (event snapshot) — varsayılan gerçek akış: görüntü + image_meta.json (köşe koordinatları, capture_time) +
    tracks.csv. Tüm zaman hesapları `reference_time` (= capture_time) üzerindendir; duvar saati kullanılmaz.
  • DEMO (DEMO_MODE=true) — duvar saatine çapalı sentetik izler + drone_meta (kamera modeli) ile georeferans.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query

from . import demo
from .analysis import Base, analyze_points
from .config import Settings, get_settings
from .dataset import Dataset, load_dataset
from .db import Persistence
from .georef import DroneMeta as GeoMeta
from .georef import bbox_center, box_anchor, pixel_to_ground, pixel_to_ground_corners
from .schemas import AnalyzeRequest, Corners, GeoDetection, GeoRequest, GeoResponse
from .store import Point, TrackStore, Vehicle, match_detections
from .timeutil import iso, parse_ts, parse_window

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("core-svc")


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    store = TrackStore(cfg)
    persistence = Persistence(cfg.database_url)
    holder: dict[str, Dataset] = {"dataset": Dataset()}

    def ts_of(value: str | float | int | None) -> float | None:
        """İstek zaman değeri (ISO | epoch | 'HH:MM') -> epoch; None -> None. Geçersizse 422."""
        if value is None:
            return None
        try:
            return parse_ts(value, base_date=cfg.dataset_date)
        except ValueError as exc:
            raise HTTPException(422, f"Geçersiz zaman: {exc}") from exc

    def default_base() -> Base:
        return Base(cfg.base_lat, cfg.base_lon, cfg.base_radius_m)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if cfg.demo_mode:
            demo.seed_demo(store, cfg)
            log.info("DEMO_MODE: demo senaryosu yüklendi: %s", [v.id for v in store.list_vehicles()])
        ds = load_dataset(cfg)
        holder["dataset"] = ds
        for tid, pts in ds.tracks.items():
            store.put_vehicle(Vehicle(id=tid, cls="unknown", points=list(pts), catalog=True))
        if persistence.enabled:
            log.info("DB kalıcılığı açık (write-through)")
            if ds.tracks:
                await asyncio.to_thread(persistence.sync_catalog, ds.tracks)
        yield

    app = FastAPI(title="core-svc", version="2.0.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        ds = holder["dataset"]
        return {
            "status": "ok",
            "service": "core-svc",
            "vehicles": len(store.list_vehicles()),
            "db": persistence.enabled,
            "demo_mode": cfg.demo_mode,
            "dataset": {"images": len(ds.images), "tracks": len(ds.tracks)},
        }

    # ------------------------------------------------------------------------------ veri seti kataloğu
    @app.get("/dataset/images")
    def dataset_images() -> dict:
        ds = holder["dataset"]
        items = [ds.image_summary(m) for m in sorted(ds.images.values(), key=lambda m: (m.capture_ts, m.image_id))]
        return {"dataset_date": cfg.dataset_date, "n_tracks": len(ds.tracks), "images": items}

    @app.get("/dataset/images/{image_id}")
    def dataset_image(image_id: str) -> dict:
        ds = holder["dataset"]
        meta = ds.images.get(image_id)
        if meta is None:
            raise HTTPException(404, f"Veri setinde image_id yok: {image_id}")
        return ds.image_summary(meta)

    @app.get("/dataset/tracks")
    def dataset_tracks() -> dict:
        ds = holder["dataset"]
        return {
            "tracks": [
                {"track_id": tid, "n_points": len(pts), "first": iso(pts[0][0]), "last": iso(pts[-1][0])}
                for tid, pts in sorted(ds.tracks.items())
            ]
        }

    # ------------------------------------------------------------------------------ georeferans
    @app.post("/georeference", response_model=GeoResponse, response_model_by_alias=True)
    def georeference(req: GeoRequest, bg: BackgroundTasks) -> GeoResponse:
        """bbox → GPS. Köşe koordinatları (bilinear) veya drone_meta (kamera modeli) ile.

        `match_tracks=true`: tespitler reference_time anındaki tracks.csv izlerine eşlenir (depo değişmez).
        `ingest=true` (eski/demo): tespitler tracker'a işlenir, araç ID'si atanır.
        """
        ds = holder["dataset"]
        w = h = 0
        corners = None
        capture_raw: str | None = None
        drone_meta = None

        if req.image_meta is not None:
            im = req.image_meta
            corners, w, h, capture_raw = im.corner_coordinates, im.width_px, im.height_px, im.capture_time
            method = "corners"
        elif req.drone_meta is not None:
            drone_meta, method = req.drone_meta, "drone_meta"
        elif req.image_id in ds.images:
            cat = ds.images[req.image_id]
            c = cat.corners
            corners = Corners(top_left=c.top_left, top_right=c.top_right, bottom_left=c.bottom_left, bottom_right=c.bottom_right)
            w, h, capture_raw = cat.width_px, cat.height_px, cat.capture_time
            method = "corners"
        else:
            raise HTTPException(422, "Georeferans için image_meta, drone_meta veya veri setinde kayıtlı bir image_id gerekli")

        ts = ts_of(req.reference_time if req.reference_time is not None else req.timestamp)
        if ts is None and capture_raw is not None:
            ts = ts_of(capture_raw)
        if ts is None:
            if method == "drone_meta":
                ts = time.time()  # yalnızca eski/demo yolu: canlı akış varsayımı
            else:
                raise HTTPException(422, "reference_time (capture_time) gerekli")

        dets: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for i, b in enumerate(req.boxes):
            if corners is not None:
                u, v = bbox_center(b.x1, b.y1, b.x2, b.y2)
                lat, lon = pixel_to_ground_corners(u, v, corners.top_left, corners.top_right, corners.bottom_left, w, h)
            else:
                assert drone_meta is not None
                m = drone_meta
                meta = GeoMeta(m.lat, m.lon, m.alt, m.heading, m.gimbal_pitch, m.fov, m.sensor_w, m.sensor_h)
                u, v = box_anchor(b.x1, b.y1, b.x2, b.y2, m.gimbal_pitch)
                ground = pixel_to_ground(u, v, meta)
                if ground is None:
                    skipped.append({"box_index": i, "reason": "ışın zemine inmiyor (ufuk üstü)"})
                    continue
                lat, lon = ground
            dets.append(
                {
                    "class": b.cls,
                    "conf": b.conf,
                    "lat": round(lat, 7),
                    "lon": round(lon, 7),
                    "box_index": i,
                    "bbox": {"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2},
                    "vehicle_id": None,
                    "match_distance_m": None,
                    "match_method": None,
                }
            )

        detection_id = store.new_event_id()
        ingested = False
        if req.match_tracks and dets:
            for d, mt in zip(dets, match_detections(store, [(d["lat"], d["lon"]) for d in dets], ts, cfg.match_gate_m, cfg.match_max_extrap_s)):
                if mt is not None:
                    d["vehicle_id"], d["match_distance_m"], d["match_method"] = mt.vehicle_id, round(mt.distance_m, 1), mt.method
        elif req.ingest and dets:
            for d, vid in zip(dets, store.associate(dets, ts)):
                d["vehicle_id"] = vid
            ingested = True

        event = {
            "detection_id": detection_id,
            "image_id": req.image_id,
            "drone_id": req.drone_id,
            "zone_id": req.zone_id,
            "ts": ts,
            "timestamp": iso(ts),
            "reference_time": iso(ts),
            "capture_time": capture_raw,
            "georef_method": method,
            "drone_meta": drone_meta.model_dump() if drone_meta is not None else None,
            "image": {"width_px": w, "height_px": h, "corner_coordinates": corners.model_dump()} if corners is not None else None,
            "detections": dets,
            "vehicle_ids": sorted({d["vehicle_id"] for d in dets if d["vehicle_id"]}),
            "ingested": ingested,
        }
        store.record_event(event)
        if persistence.enabled:
            bg.add_task(persistence.save_event, event)

        log.info("georef image=%s method=%s ref=%s dets=%d skipped=%d vehicles=%s", req.image_id, method, event["reference_time"], len(dets), len(skipped), event["vehicle_ids"])
        return GeoResponse(
            detection_id=detection_id,
            image_id=req.image_id,
            timestamp=event["timestamp"],
            reference_time=event["reference_time"],
            capture_time=capture_raw,
            georef_method=method,
            zone_id=req.zone_id,
            detections=[
                GeoDetection(cls=d["class"], conf=d["conf"], lat=d["lat"], lon=d["lon"], vehicle_id=d["vehicle_id"], box_index=d["box_index"], match_distance_m=d["match_distance_m"], match_method=d["match_method"])
                for d in dets
            ],
            skipped=skipped,
            ingested=ingested,
        )

    # ------------------------------------------------------------------------------ tracking
    def points_json(pts: list[Point]) -> list[dict[str, Any]]:
        return [{"lat": p[1], "lon": p[2], "ts": iso(p[0])} for p in pts]

    @app.get("/tracks/{vehicle_id}")
    def get_track(
        vehicle_id: str,
        window: str = Query("2h", description="örn. 2h, 30m, 90s"),
        until: str | None = Query(None, description="Referans zaman (ISO | 'HH:MM'); sonrası kesilir. Yoksa iz sonu"),
    ) -> dict:
        try:
            window_s = parse_window(window)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        until_ts = ts_of(until)
        pts = store.points(vehicle_id, window_s, until_ts)
        if pts is None:
            raise HTTPException(404, f"Bilinmeyen araç: {vehicle_id}")
        v = store.get(vehicle_id)
        return {
            "vehicle_id": vehicle_id,
            "class": v.cls if v else None,
            "window_s": window_s,
            "until": iso(until_ts) if until_ts is not None else None,
            "points": points_json(pts),
        }

    @app.post("/tracks/analyze")
    def analyze(req: AnalyzeRequest) -> dict:
        """Hız, yön, yaklaşma, ETA — `reference_time` anındaki bilgiyle (gelecek nokta kullanılmaz)."""
        base = default_base()
        if req.base_location:
            bl = req.base_location
            base = Base(bl.lat, bl.lon, bl.radius_m if bl.radius_m is not None else base.radius_m)
        ref = ts_of(req.reference_time)

        if req.vehicle_id:
            pts = store.points(req.vehicle_id, None, ref)
            if pts is None:
                raise HTTPException(404, f"Bilinmeyen araç: {req.vehicle_id}")
            if ref is None:
                ref = pts[-1][0] if pts else 0.0
            cp = req.current_position
            if cp is not None and (not pts or ref - pts[-1][0] >= 1.0):
                pts.append((ref, cp.lat, cp.lon))  # iz reference_time'a ulaşmıyor: görüntüdeki güncel konum son nokta
            result = analyze_points(pts[-200:], base, cfg, ref)
            result["vehicle_id"] = req.vehicle_id
        else:
            assert req.coords is not None
            try:
                raw = [(parse_ts(c.ts, base_date=cfg.dataset_date), c.lat, c.lon) for c in req.coords if c.ts is not None]
            except ValueError as exc:
                raise HTTPException(422, f"coords zaman damgası geçersiz: {exc}") from exc
            if len(raw) != len(req.coords):
                raise HTTPException(422, "coords içindeki her noktada ts zorunlu")
            raw.sort()
            if ref is None:
                ref = raw[-1][0]
            raw = [p for p in raw if p[0] <= ref]
            result = analyze_points(raw, base, cfg, ref)
            result["vehicle_id"] = None
        result["reference_time"] = iso(ref)
        result["base"] = {"lat": base.lat, "lon": base.lon, "radius_m": base.radius_m}
        return result

    @app.get("/vehicles")
    def list_vehicles(
        trace_window: str = Query("30m"),
        max_points: int = Query(80, ge=2, le=1000),
        until: str | None = Query(None, description="Referans zaman; yoksa depodaki en son nokta"),
    ) -> dict:
        """Tüm araçlar: son konum + hareket özeti + (seyreltilmiş) iz. Duvar saati kullanılmaz."""
        try:
            window_s = parse_window(trace_window)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        until_ts = ts_of(until)
        ref = until_ts if until_ts is not None else store.last_ts()
        base = default_base()
        out = []
        for v in store.list_vehicles():
            pts = store.points(v.id, None, until_ts) or []
            if not pts:
                continue
            trace = store.points(v.id, window_s, until_ts) or []
            if len(trace) > max_points:
                step = len(trace) / (max_points - 1)
                trace = [trace[int(i * step)] for i in range(max_points - 1)] + [trace[-1]]
            analysis = analyze_points(pts[-200:], base, cfg, ref if ref is not None else pts[-1][0])
            out.append(
                {
                    "vehicle_id": v.id,
                    "class": v.cls,
                    "demo": v.demo,
                    "last_seen": iso(pts[-1][0]),
                    "last_position": {"lat": pts[-1][1], "lon": pts[-1][2]},
                    "n_points": len(pts),
                    "trace": points_json(trace),
                    "analysis": {k: analysis[k] for k in ("speed_mps", "heading_deg", "approaching", "eta_min", "distance_to_base_m", "closing_speed_mps", "stale")},
                }
            )
        return {"base": {"lat": base.lat, "lon": base.lon, "radius_m": base.radius_m}, "vehicles": out}

    # ------------------------------------------------------------------------------ tespit olayları
    @app.get("/detections/{detection_id}")
    def get_detection(detection_id: str) -> dict:
        ev = store.get_event(detection_id)
        if ev is None:
            raise HTTPException(404, f"Bilinmeyen detection_id: {detection_id}")
        return ev

    @app.get("/detections")
    def list_detections(limit: int = Query(10, ge=1, le=100)) -> dict:
        return {"detections": store.list_events(limit)}

    # ------------------------------------------------------------------------------ demo yönetimi
    @app.post("/admin/demo/reset")
    def reset_demo() -> dict:
        """(DEMO_MODE) Demo izlerini yeniden 'şimdi'ye çapalar. Veri seti izleri korunur."""
        if not cfg.enable_admin:
            raise HTTPException(403, "ENABLE_ADMIN=false")
        if not cfg.demo_mode:
            raise HTTPException(403, "DEMO_MODE=false: demo sıfırlama yalnızca demo modunda açıktır")
        anchor = demo.seed_demo(store, cfg)
        return {"anchor": iso(anchor), "vehicles": [v.id for v in store.list_vehicles() if v.demo]}

    app.state.store = store
    app.state.dataset = holder
    return app


app = create_app()
