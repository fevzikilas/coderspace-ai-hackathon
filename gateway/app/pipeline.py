"""Pipeline orkestrasyonu.

İki akış:
  • OLAY (event snapshot): image_id (veri seti) veya image_meta + görüntü → olay_context → detect → georef(köşe
    koordinatları) + iz eşleştirme → tracks → pattern → assess. Tüm zaman hesapları görüntünün `capture_time`'ı
    (= reference_time) üzerindendir; duvar saati KULLANILMAZ.
  • DRONE/DEMO (eski): drone_id → drone_context → detect → georef(kamera modeli) → … Zaman = şimdi (demo semantiği).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .config import Settings
from .services import ServiceError, Services
from .state import GatewayState, now_iso

EVENT_STEPS = ["event_context", "detect", "georeference", "tracks", "vehicle_links", "pattern", "assess"]
DRONE_STEPS = ["drone_context", "detect", "georeference", "tracks", "pattern", "assess"]
MAX_TRACK_POINTS = 120


class PipelineRequest(BaseModel):
    # ---- olay akışı
    image_id: str | None = Field(None, description="Veri setindeki görüntü (image_meta.json anahtarı). Yüklenen görüntünün dosya adı bununla eşleşmelidir")
    image_b64: str | None = Field(None, description="Base64 görüntü (image_id verilirse veri seti görüntüsü yerine bu kullanılır)")
    image_url: str | None = None
    image_meta: dict[str, Any] | None = Field(None, description="Satır içi meta: {width_px,height_px,capture_time,corner_coordinates{top_left,top_right,bottom_left,bottom_right}}")
    capture_time: str | None = Field(None, description="Satır içi meta için 'HH:MM' | ISO-8601")
    zone_id: str | None = Field(None, description="Verilmezse görüntü konumuna en yakın bölge çözülür")
    # ---- eski/demo akışı
    drone_id: str | None = None
    drone_meta: dict[str, float] | None = Field(None, description="Drone kaydındaki değerleri ezmek için (eski akış)")
    timestamp: str | None = Field(None, description="Eski akış: çekim zamanı (yoksa şimdi)")
    reset_demo: bool = Field(False, description="DEMO_MODE: core-svc demo izlerini 'şimdi'ye yeniden çapalar")

    @model_validator(mode="after")
    def _has_source(self) -> "PipelineRequest":
        if not (self.image_id or self.image_meta or self.drone_id):
            raise ValueError("image_id (olay) veya drone_id (eski/demo akış) verilmeli")
        return self

    @property
    def mode(self) -> str:
        return "event" if (self.image_id or self.image_meta) else "drone"


class PipelineError(Exception):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


def new_run(req: PipelineRequest, zone_id: str | None) -> dict[str, Any]:
    steps = EVENT_STEPS if req.mode == "event" else DRONE_STEPS
    return {
        "run_id": "run-" + uuid.uuid4().hex[:10],
        "mode": req.mode,
        "status": "queued",
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "duration_ms": None,
        "request": {
            "image_id": req.image_id,
            "drone_id": req.drone_id,
            "zone_id": zone_id,
            "has_image": bool(req.image_b64 or req.image_url),
            "reset_demo": req.reset_demo,
        },
        "steps": [{"name": n, "status": "pending", "started_at": None, "duration_ms": None, "detail": None, "error": None} for n in steps],
        "result": {
            "event": None,
            "drone": None,
            "image": None,
            "detection_id": None,
            "detections": [],
            "vehicles": {},
            "pattern": None,
            "assessment": None,
            "candidate_vehicle_links": [],
            "vehicle_graph": {"relation_semantics": "candidate_edges_are_independent_not_identity_clusters", "nodes": [], "edges": []},
            "vehicle_link_diagnostics": None,
            "message": None,
        },
        "error": None,
    }


def _downsample(points: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    if len(points) <= n:
        return points
    step = len(points) / (n - 1)
    return [points[int(i * step)] for i in range(n - 1)] + [points[-1]]


class Pipeline:
    def __init__(self, s: Settings, svc: Services, state: GatewayState) -> None:
        self._s = s
        self._svc = svc
        self._state = state
        self._sem = asyncio.Semaphore(s.max_concurrent_runs)

    @property
    def default_base(self) -> dict[str, float]:
        """Eski/demo akışının üssü (ortam varsayılanı). Olay akışında üs zones.json'dan gelir."""
        return {"lat": self._s.base_lat, "lon": self._s.base_lon, "radius_m": self._s.base_radius_m}

    # ------------------------------------------------------------------ dış giriş
    async def run(self, run: dict[str, Any], req: PipelineRequest) -> dict[str, Any]:
        """Koşuyu yürütür; run sözlüğünü yerinde günceller (UI polling ara durumu görebilsin diye)."""
        async with self._sem:
            run["status"] = "running"
            run["started_at"] = now_iso()
            t0 = time.perf_counter()
            self._state.log("info", f"Pipeline başladı ({run['mode']}): {req.image_id or req.drone_id}", run["run_id"])
            try:
                async with asyncio.timeout(self._s.pipeline_timeout_s):
                    await self._execute(run, req)
                run["status"] = "succeeded"
                self._state.log("info", "Pipeline tamamlandı", run["run_id"])
            except PipelineError as exc:
                run["status"], run["error"] = "failed", {"step": exc.step, "message": exc.message}
                self._state.log("error", f"Pipeline başarısız [{exc.step}]: {exc.message}", run["run_id"], exc.step)
            except TimeoutError:
                run["status"], run["error"] = "failed", {"step": None, "message": f"pipeline zaman aşımı ({self._s.pipeline_timeout_s:g} sn)"}
                self._state.log("error", "Pipeline zaman aşımına uğradı", run["run_id"])
            except Exception as exc:  # noqa: BLE001 — beklenmeyen hata da koşuya yansır
                run["status"], run["error"] = "failed", {"step": None, "message": f"beklenmeyen hata: {exc}"}
                self._state.log("error", f"Beklenmeyen hata: {exc}", run["run_id"])
            finally:
                for st in run["steps"]:
                    if st["status"] in {"pending", "running"}:
                        st["status"] = "skipped" if st["status"] == "pending" else "failed"
                run["finished_at"] = now_iso()
                run["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return run

    # ------------------------------------------------------------------ adım yardımcısı
    async def _step(self, run: dict[str, Any], name: str, fn):
        st = next(x for x in run["steps"] if x["name"] == name)
        st["status"], st["started_at"] = "running", now_iso()
        t0 = time.perf_counter()
        try:
            out, detail = await fn()
        except ServiceError as exc:
            st["status"], st["error"] = "failed", str(exc)
            st["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            raise PipelineError(name, str(exc)) from exc
        st["status"], st["detail"] = "succeeded", detail
        st["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self._state.log("info", detail, run["run_id"], name)
        return out

    @staticmethod
    def _skip(run: dict[str, Any], *names: str) -> None:
        for name in names:
            next(x for x in run["steps"] if x["name"] == name)["status"] = "skipped"

    # ------------------------------------------------------------------ akış
    async def _execute(self, run: dict[str, Any], req: PipelineRequest) -> None:
        if req.mode == "event":
            ctx = await self._event_context(run, req)
        else:
            ctx = await self._drone_context(run, req)
        res = run["result"]

        det = await self._detect(run, req, ctx)
        geo = await self._georeference(run, req, ctx, det)

        boxes = det["boxes"]
        res["detections"] = [
            {
                "vehicle_id": d.get("vehicle_id"), "class": d["class"], "conf": d["conf"], "lat": d["lat"], "lon": d["lon"],
                "box_index": d["box_index"], "match_distance_m": d.get("match_distance_m"), "match_method": d.get("match_method"),
                "bbox": {k: boxes[d["box_index"]][k] for k in ("x1", "y1", "x2", "y2")},
            }
            for d in geo["detections"]
        ]
        res["boxes"] = boxes
        vehicle_ids = sorted({d["vehicle_id"] for d in res["detections"] if d["vehicle_id"]})
        if not vehicle_ids:
            res["message"] = (
                "Görüntüde araç tespit edilmedi; risk değerlendirmesi yapılmadı."
                if not res["detections"]
                else f"{len(res['detections'])} nesne tespit edildi ancak izle (tracks.csv) eşleşen araç yok; risk değerlendirmesi yapılmadı."
            )
            self._skip(run, "tracks", *(["vehicle_links"] if req.mode == "event" else []), "pattern", "assess")
            return

        await self._tracks(run, ctx, vehicle_ids)
        if req.mode == "event":
            await self._vehicle_links(run, ctx, det["image_id"])
        await self._pattern(run, ctx, vehicle_ids)
        await self._assess(run, ctx, geo)

    # ------------------------------------------------------------------ 1) bağlam
    async def _event_context(self, run: dict[str, Any], req: PipelineRequest) -> dict[str, Any]:
        svc, res = self._svc, run["result"]

        async def step():
            if req.image_meta is not None:
                m = req.image_meta
                cap = req.capture_time or m.get("capture_time")
                if not cap:
                    raise ServiceError("gateway", "satır içi image_meta için capture_time gerekli")
                try:
                    c = m["corner_coordinates"]
                    pts = [c["top_left"], c["top_right"], c["bottom_left"], c["bottom_right"]]
                    center = {"lat": sum(p[0] for p in pts) / 4, "lon": sum(p[1] for p in pts) / 4}
                except (KeyError, TypeError, IndexError) as exc:
                    raise ServiceError("gateway", f"image_meta.corner_coordinates geçersiz: {exc}") from exc
                meta = {"image_id": req.image_id, "capture_time": cap, "capture_iso": None, "center": center,
                        "width_px": m.get("width_px"), "height_px": m.get("height_px"), "corner_coordinates": c, "footprint_m": None}
                ref = cap
            else:
                meta = await svc.call("core", "GET", f"/dataset/images/{req.image_id}")
                ref = meta["capture_iso"]

            base = await svc.call("mock", "GET", "/base")
            if req.zone_id:
                z = await svc.call("mock", "GET", f"/zones/{req.zone_id}")
                zone = {"zone_id": z["zone_id"], "name": z["name"], "distance_m": None}
            else:
                a = await svc.call("mock", "POST", "/zones/assign", json={"points": [meta["center"]]})
                zone = a["assignments"][0]
            ctx = {"mode": "event", "base": {"lat": base["lat"], "lon": base["lon"], "radius_m": base["radius_m"]}, "zone_id": zone["zone_id"], "reference_time": ref}
            run["request"]["zone_id"] = zone["zone_id"]
            res["event"] = {
                "image_id": meta["image_id"], "capture_time": meta["capture_time"], "reference_time": ref if meta["capture_iso"] else None,
                "center": meta["center"], "footprint_m": meta.get("footprint_m"), "width_px": meta.get("width_px"), "height_px": meta.get("height_px"),
                "corner_coordinates": meta["corner_coordinates"],
                "zone": {"zone_id": zone["zone_id"], "name": zone["name"], "distance_m": zone.get("distance_m")},
                "base": {**ctx["base"], "name": base.get("name"), "alert_radius_m": base.get("alert_radius_m")},
            }
            return ctx, f"{meta['image_id'] or 'yüklenen görüntü'} · {meta['capture_time']} itibarıyla · bölge: {zone['name']} · üs: {base.get('name')}"

        return await self._step(run, "event_context", step)

    async def _drone_context(self, run: dict[str, Any], req: PipelineRequest) -> dict[str, Any]:
        svc, res = self._svc, run["result"]
        zone_id = run["request"]["zone_id"] or self._s.default_zone_id
        run["request"]["zone_id"] = zone_id
        if req.reset_demo:
            try:
                info = await svc.call("core", "POST", "/admin/demo/reset")
                self._state.log("info", f"Demo senaryosu sıfırlandı (çapa {info.get('anchor')})", run["run_id"])
                self._state.sweep.clear()
            except ServiceError as exc:
                raise PipelineError("drone_context", f"demo sıfırlanamadı: {exc}") from exc

        async def step():
            data = await svc.call("mock", "GET", "/drones")
            drone = next((d for d in data["drones"] if d["id"].upper() == (req.drone_id or "").upper()), None)
            if drone is None:
                raise ServiceError("mock", f"bilinmeyen drone: {req.drone_id}", 404)
            if str(drone.get("status", "")).upper() == "OFFLINE":
                raise ServiceError("mock", f"{drone['id']} çevrimdışı; görüntü alınamaz", 409)
            meta = {
                "lat": drone["lat"], "lon": drone["lon"], "alt": drone.get("alt") or 100.0, "heading": drone["heading"],
                "gimbal_pitch": drone.get("gimbal_pitch", -90.0), "fov": drone["fov"],
                "sensor_w": drone.get("sensor_w", 1920), "sensor_h": drone.get("sensor_h", 1080),
            }
            if req.drone_meta:
                meta.update({k: v for k, v in req.drone_meta.items() if k in meta})
            ctx = {"mode": "drone", "base": self.default_base, "zone_id": zone_id, "reference_time": req.timestamp or now_iso(), "drone_meta": meta}
            res["drone"] = {**drone, "meta": meta}
            return ctx, f"{drone['id']} ({drone.get('status')}) irtifa {meta['alt']:.0f} m, gimbal {meta['gimbal_pitch']:.0f}°"

        return await self._step(run, "drone_context", step)

    # ------------------------------------------------------------------ 2) detection
    async def _detect(self, run: dict[str, Any], req: PipelineRequest, ctx: dict[str, Any]) -> dict[str, Any]:
        res = run["result"]

        async def step():
            body: dict[str, Any] = {"timestamp": ctx["reference_time"]}
            if req.image_id:
                body["image_id"] = req.image_id
            if req.drone_id:
                body["drone_id"] = req.drone_id
            if req.image_b64:
                body["image_b64"] = req.image_b64
            elif req.image_url:
                body["image_url"] = req.image_url
            out = await self._svc.call("detection", "POST", "/detect", json=body)
            return out, f"{len(out['boxes'])} kutu ({out['mode']}/{out.get('backend', '?')}, {out['image_width']}x{out['image_height']}, {out['inference_ms']:.0f} ms)"

        det = await self._step(run, "detect", step)
        has_image = bool(req.image_b64 or req.image_url or req.image_id)
        res["image"] = {
            "image_id": det["image_id"],
            "url": f"/api/images/{det['image_id']}" if has_image else None,
            "width": det["image_width"],
            "height": det["image_height"],
            "mode": det["mode"],
            "backend": det.get("backend"),
            "fallback_reason": det.get("fallback_reason"),
            "timestamp": det["timestamp"],
        }
        return det

    # ------------------------------------------------------------------ 3) georeferans (+ olayda iz eşleştirme)
    async def _georeference(self, run: dict[str, Any], req: PipelineRequest, ctx: dict[str, Any], det: dict[str, Any]) -> dict[str, Any]:
        res = run["result"]

        async def step():
            body: dict[str, Any] = {"image_id": det["image_id"], "boxes": det["boxes"], "drone_id": req.drone_id, "zone_id": ctx["zone_id"], "reference_time": ctx["reference_time"]}
            if ctx["mode"] == "event":
                body.update(match_tracks=True, ingest=False)
                if req.image_meta is not None:
                    body["image_meta"] = {**req.image_meta, "capture_time": ctx["reference_time"]}
            else:
                meta = dict(ctx["drone_meta"])
                meta["sensor_w"], meta["sensor_h"] = det["image_width"], det["image_height"]  # gerçek görüntü boyutu
                body["drone_meta"] = meta
                body["timestamp"] = ctx["reference_time"]
            out = await self._svc.call("core", "POST", "/georeference", json=body)
            vids = sorted({d["vehicle_id"] for d in out["detections"] if d.get("vehicle_id")})
            return out, f"{len(out['detections'])} tespit GPS'e çevrildi ({out.get('georef_method')}), araçlar: {', '.join(vids) or '—'}"

        geo = await self._step(run, "georeference", step)
        res["detection_id"] = geo["detection_id"]
        if ctx["mode"] == "event":
            ctx["reference_time"] = geo.get("reference_time") or ctx["reference_time"]  # sonraki adımlar ISO kullanır
            if res["event"]:
                res["event"]["reference_time"] = ctx["reference_time"]
        return geo

    # ------------------------------------------------------------------ 4) iz + hareket analizi
    async def _tracks(self, run: dict[str, Any], ctx: dict[str, Any], vehicle_ids: list[str]) -> None:
        res, svc = run["result"], self._svc
        ref = ctx["reference_time"]
        pos = {d["vehicle_id"]: {"lat": d["lat"], "lon": d["lon"]} for d in res["detections"] if d["vehicle_id"]}
        cls = {d["vehicle_id"]: d["class"] for d in res["detections"] if d["vehicle_id"]}

        async def step():
            async def one(vid: str):
                tparams = {"window": "2h", **({"until": ref} if ctx["mode"] == "event" else {})}
                abody: dict[str, Any] = {"vehicle_id": vid, "base_location": ctx["base"], "reference_time": ref}
                if ctx["mode"] == "event":
                    abody["current_position"] = pos[vid]
                track, analysis = await asyncio.gather(
                    svc.call("core", "GET", f"/tracks/{vid}", params=tparams),
                    svc.call("core", "POST", "/tracks/analyze", json=abody),
                )
                return vid, {"class": cls.get(vid) or track.get("class"), "track": _downsample(track["points"], MAX_TRACK_POINTS), "analysis": analysis, "pattern": None, "matched_patterns": []}

            out = dict(await asyncio.gather(*(one(v) for v in vehicle_ids)))
            appr = [v for v, x in out.items() if x["analysis"].get("approaching")]
            return out, f"{len(out)} aracın izi + hareket analizi; yaklaşan: {', '.join(appr) or 'yok'}"

        res["vehicles"] = await self._step(run, "tracks", step)

    # ------------------------------------------------------------------ cross-event appearance evidence (olay akışı)
    async def _vehicle_links(self, run: dict[str, Any], ctx: dict[str, Any], image_id: str) -> None:
        res = run["result"]

        async def step():
            tracked = [d for d in res["detections"] if d.get("vehicle_id")]
            body = {
                "image_id": image_id,
                "crops": [{"track_id": d["vehicle_id"], "bbox": d["bbox"]} for d in tracked],
            }
            try:
                appearance = await self._svc.call("detection", "POST", "/appearance/embed", json=body)
            except ServiceError as exc:
                res["vehicle_link_diagnostics"] = {"status": "unavailable", "reason": str(exc)}
                return None, f"Araç görünüm kanıtı kullanılamadı; ana pipeline devam etti ({exc})"

            by_track = {d["vehicle_id"]: d for d in tracked}
            event_id = (res.get("event") or {}).get("image_id") or image_id
            observations = []
            for item in appearance.get("tracks", []):
                detection = by_track.get(item.get("track_id"))
                if detection is None:
                    continue
                observations.append(
                    {
                        "event_id": event_id,
                        "track_id": item["track_id"],
                        "timestamp": ctx["reference_time"],
                        "position": {"lat": detection["lat"], "lon": detection["lon"]},
                        "class": detection["class"],
                        "crop": item.get("crop"),
                        "quality": item.get("quality"),
                        "model": item.get("model") or appearance.get("model"),
                        "embedding": item.get("embedding") or [],
                    }
                )
            links, graph, stats = self._state.vehicle_evidence.add_and_match(observations)
            res["candidate_vehicle_links"] = links
            res["vehicle_graph"] = graph
            res["vehicle_link_diagnostics"] = {"status": "ok", **stats, "observations": len(observations)}
            return None, f"{len(observations)} crop temsil edildi; {stats['accepted']} cross-event görsel aday kabul edildi"

        await self._step(run, "vehicle_links", step)

    # ------------------------------------------------------------------ 5) patern
    async def _pattern(self, run: dict[str, Any], ctx: dict[str, Any], vehicle_ids: list[str]) -> None:
        res = run["result"]

        async def step():
            body: dict[str, Any] = {"vehicle_ids": vehicle_ids, "zone_id": ctx["zone_id"], "base_location": ctx["base"]}
            if ctx["mode"] == "event":
                body["reference_time"] = ctx["reference_time"]
            out = await self._svc.call("pattern", "POST", "/pattern/classify", json=body)
            for vid, info in ((out.get("detail") or {}).get("per_vehicle") or {}).items():
                if vid in res["vehicles"]:
                    res["vehicles"][vid]["pattern"] = info.get("pattern")
                    res["vehicles"][vid]["matched_patterns"] = info.get("matched", [])
            if ctx["mode"] == "drone":
                await self._sweep(ctx)  # harita etiketleri için tüm bölge (yalnızca eski akış; best-effort)
            return out, f"{out['pattern']} (güven {out['confidence']:.2f}) — {', '.join(out['involved_vehicles'])}"

        res["pattern"] = await self._step(run, "pattern", step)

    # ------------------------------------------------------------------ 6) risk değerlendirmesi
    async def _assess(self, run: dict[str, Any], ctx: dict[str, Any], geo: dict[str, Any]) -> None:
        async def step():
            out = await self._svc.call(
                "risk", "POST", "/assess",
                json={
                    "zone_id": ctx["zone_id"],
                    "detection_id": geo["detection_id"],
                    "base_location": ctx["base"],
                    "vehicle_link_evidence": run["result"].get("candidate_vehicle_links", []),
                },
                timeout=self._s.risk_timeout_s,
            )
            return out, f"risk={out['risk_level']} güven={out['confidence']:.2f} mod={out['mode']} ({len(out['tool_calls_log'])} araç çağrısı)"

        run["result"]["assessment"] = await self._step(run, "assess", step)

    async def _sweep(self, ctx: dict[str, Any]) -> None:
        """Bilinen tüm araçları tek çağrıda sınıflandırıp per-vehicle etiketleri saklar (eski akış, best-effort)."""
        try:
            vs = await self._svc.call("core", "GET", "/vehicles", params={"trace_window": "1m", "max_points": 2})
            ids = [v["vehicle_id"] for v in vs["vehicles"]]
            if not ids:
                return
            out = await self._svc.call("pattern", "POST", "/pattern/classify", json={"vehicle_ids": ids, "zone_id": ctx["zone_id"], "base_location": ctx["base"]})
            per = (out.get("detail") or {}).get("per_vehicle", {})
            self._state.sweep = {vid: {"pattern": info.get("pattern"), "matched": info.get("matched", [])} for vid, info in per.items()}
            self._state.sweep_at = now_iso()
        except ServiceError as exc:
            self._state.log("warn", f"Bölge taraması atlandı: {exc}")
