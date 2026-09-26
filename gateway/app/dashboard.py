"""`GET /dashboard/state` (harita/panel agregasyonu) ve `GET /events` (olay kataloğu)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .config import Settings
from .services import ServiceError, Services
from .state import GatewayState, now_iso


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    res = run.get("result") or {}
    a = res.get("assessment")
    ev = res.get("event") or {}
    return {
        "run_id": run["run_id"],
        "mode": run.get("mode"),
        "status": run["status"],
        "created_at": run["created_at"],
        "duration_ms": run["duration_ms"],
        "image_id": run["request"].get("image_id"),
        "capture_time": ev.get("capture_time"),
        "drone_id": run["request"].get("drone_id"),
        "zone_id": run["request"].get("zone_id"),
        "risk_level": a["risk_level"] if a else None,
        "pattern": (res.get("pattern") or {}).get("pattern"),
    }


_ANALYSIS_KEYS = ("speed_mps", "heading_deg", "approaching", "eta_min", "distance_to_base_m", "closing_speed_mps", "stale")


class Dashboard:
    def __init__(self, s: Settings, svc: Services, state: GatewayState) -> None:
        self._s = s
        self._svc = svc
        self._state = state

    # ------------------------------------------------------------------ sağlık
    async def _health_one(self, name: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            r = await self._svc.raw(name, "GET", "/health", timeout=1.5)
            ok = r.status_code == 200
            info = r.json() if ok else {}
            degraded = ok and info.get("status") == "degraded"
            return {"status": "down" if not ok else ("degraded" if degraded else "up"), "latency_ms": round((time.perf_counter() - t0) * 1000), "info": {k: v for k, v in info.items() if k not in {"status", "service"}}}
        except (ServiceError, ValueError) as exc:
            return {"status": "down", "latency_ms": None, "error": str(exc)}

    async def health(self) -> dict[str, Any]:
        async def fetch():
            names = list(Services.NAMES)
            results = await asyncio.gather(*(self._health_one(n) for n in names))
            return dict(zip(names, results))

        return await self._state.cached("health", self._s.ttl_health_s, fetch)

    # ------------------------------------------------------------------ olay kataloğu
    async def events(self) -> dict[str, Any]:
        """Veri setindeki tüm görüntüler (olaylar): bölge, capture_time ve (varsa) bu oturumdaki son değerlendirme."""

        async def fetch() -> dict[str, Any]:
            cat = await self._svc.call("core", "GET", "/dataset/images")
            images = cat["images"]
            zones: dict[str, Any] = {"zones": [], "base": None}
            assign: list[dict[str, Any]] = []
            if images:
                try:
                    zones = await self._svc.call("mock", "GET", "/zones")
                    assign = (await self._svc.call("mock", "POST", "/zones/assign", json={"points": [i["center"] for i in images]}))["assignments"]
                except ServiceError as exc:
                    self._state.log("warn", f"Bölge çözümlenemedi: {exc}")
            order = {z["zone_id"]: n for n, z in enumerate(zones.get("zones", []))}
            events = []
            for i, img in enumerate(images):
                a = assign[i] if i < len(assign) else None
                events.append({**img, "zone_id": a["zone_id"] if a else None, "zone_name": a["name"] if a else None})
            events.sort(key=lambda e: (order.get(e["zone_id"], 99), e["capture_iso"], e["image_id"]))
            return {"dataset_date": cat.get("dataset_date"), "errors": cat.get("errors", []), "n_tracks": cat.get("n_tracks", 0), "base": zones.get("base"), "zones": zones.get("zones", []), "events": events}

        data = await self._state.cached("events", self._s.ttl_slow_s, fetch)
        last: dict[str, dict[str, Any]] = {}
        for run in self._state.runs.values():  # eski -> yeni; sonuncusu kazanır
            iid = run["request"].get("image_id")
            if iid:
                last[iid] = _run_summary(run)
        return {**data, "events": [{**e, "last_run": last.get(e["image_id"])} for e in data["events"]]}

    # ------------------------------------------------------------------ dashboard
    def _event_vehicles(self, run: dict[str, Any], assessment: dict[str, Any] | None) -> list[dict[str, Any]]:
        out = []
        for vid, v in (run["result"].get("vehicles") or {}).items():
            a = v["analysis"]
            out.append(
                {
                    "vehicle_id": vid,
                    "class": v.get("class") or "unknown",
                    "demo": False,
                    "last_seen": a.get("last_seen"),
                    "last_position": a.get("last_position"),
                    "n_points": len(v["track"]),
                    "trace": v["track"],
                    "analysis": {k: a.get(k) for k in _ANALYSIS_KEYS},
                    "pattern": v.get("pattern"),
                    "matched_patterns": v.get("matched_patterns", []),
                    "in_latest_detection": True,
                    "risk_level": assessment["risk_level"] if assessment else None,
                }
            )
        return out

    async def state(self, zone_id: str | None = None) -> dict[str, Any]:
        s, st, svc = self._s, self._state, self._svc
        latest = st.latest_run()
        ev_run = latest if (latest and latest.get("mode") == "event") else None
        mode = "event" if ev_run else ("drone" if latest else "idle")
        services = await self.health()
        demo_mode = bool((services.get("core") or {}).get("info", {}).get("demo_mode"))

        errors: dict[str, str] = {}

        async def get(key: str, ttl: float, service: str, path: str, **kw: Any):
            return await st.cached(key, ttl, lambda: svc.call(service, "GET", path, **kw))

        # ---- üs + bölge
        base: dict[str, Any] = {"name": s.base_name, "lat": s.base_lat, "lon": s.base_lon, "radius_m": s.base_radius_m, "alert_radius_m": s.alert_radius_m}
        zone = zone_id or (latest["request"].get("zone_id") if latest else None) or s.default_zone_id
        ref: str | None = None
        point: dict[str, float] | None = None
        if ev_run:
            ev = ev_run["result"]["event"]
            if ev:
                base = {"name": ev["base"].get("name") or s.base_name, "lat": ev["base"]["lat"], "lon": ev["base"]["lon"], "radius_m": ev["base"]["radius_m"], "alert_radius_m": ev["base"].get("alert_radius_m") or s.alert_radius_m}
                zone = ev["zone"]["zone_id"]
                ref = ev.get("reference_time") or ev.get("capture_time")
                point = ev["center"]
        elif mode == "idle":
            try:
                b = await get("base", s.ttl_slow_s, "mock", "/base")
                if b.get("source") == "dataset":
                    base = {"name": b.get("name") or s.base_name, "lat": b["lat"], "lon": b["lon"], "radius_m": b["radius_m"], "alert_radius_m": b.get("alert_radius_m") or s.alert_radius_m}
            except ServiceError as exc:
                errors["base"] = str(exc)

        # ---- veri kaynakları (paralel, hata toleranslı)
        tasks: dict[str, Any] = {}
        if mode == "drone" or (mode == "idle" and demo_mode):
            tasks["drones"] = get("drones", s.ttl_fast_s, "mock", "/drones")
        if mode == "drone":
            tasks["vehicles"] = get("vehicles", s.ttl_fast_s, "core", "/vehicles", params={"trace_window": "30m", "max_points": 60})
        if mode in ("event", "drone"):
            iparams = {"as_of": ref} if ref else None
            rparams = {**({"as_of": ref} if ref else {}), **({"lat": point["lat"], "lon": point["lon"]} if point else {})} or None
            tasks["intel"] = get(f"intel:{zone}:{ref}", s.ttl_slow_s, "mock", f"/intel/{zone}", params=iparams)
            tasks["reports"] = get(f"reports:{zone}:{ref}:{point and round(point['lat'], 4)}", s.ttl_slow_s, "mock", f"/reports/{zone}", params=rparams)
        results = dict(zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True)))
        data: dict[str, Any] = {}
        for key, res in results.items():
            if isinstance(res, Exception):
                errors[key] = str(res)
                data[key] = None
            else:
                data[key] = res

        # ---- araçlar + değerlendirme
        if ev_run:
            assessment = ev_run["result"].get("assessment")
            vehicles = self._event_vehicles(ev_run, assessment)
        else:
            active = st.latest_finished_result()
            assessment = active["result"]["assessment"] if active else None
            in_detection = set((active["result"]["vehicles"] or {}).keys()) if active else set()
            vehicles = []
            for v in (data.get("vehicles") or {}).get("vehicles", []):
                label = st.sweep.get(v["vehicle_id"], {})
                vehicles.append(
                    {
                        **v,
                        "pattern": label.get("pattern"),
                        "matched_patterns": label.get("matched", []),
                        "in_latest_detection": v["vehicle_id"] in in_detection,
                        "risk_level": assessment["risk_level"] if assessment and v["vehicle_id"] in in_detection else None,
                    }
                )

        return {
            "server_time": now_iso(),
            "mode": mode,
            "zone_id": zone,
            "base": base,
            "event": ev_run["result"]["event"] if ev_run else None,
            "capabilities": {"demo": demo_mode, "dataset": (services.get("core") or {}).get("info", {}).get("dataset")},
            "drones": (data.get("drones") or {}).get("drones", []),
            "vehicles": vehicles,
            "intel": (data.get("intel") or {}).get("items", []),
            "reports": (data.get("reports") or {}).get("items", []),
            "latest_run": latest,
            "assessment": assessment,
            "runs": [_run_summary(r) for r in list(reversed(st.runs.values()))[:10]],
            "logs": list(st.logs)[-120:],
            "sweep_at": st.sweep_at,
            "services": services,
            "errors": errors,
        }
