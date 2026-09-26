"""gateway — BFF: tek API origin'i, tek auth katmanı, pipeline orkestrasyonu, dashboard agregasyonu, reverse-proxy.

Tüm uçlar hem kök yolda (`/pipeline/run`) hem `/api` altında (`/api/pipeline/run`) sunulur; Ingress/nginx `/api`
önekini yönlendirir, geliştirmede Vite `/api`'yi buraya proxy'ler.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import make_auth_dependency
from .config import get_settings
from .dashboard import Dashboard, _run_summary
from .pipeline import Pipeline, PipelineRequest, new_run
from .services import ServiceError, Services
from .state import GatewayState

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("gateway")

settings = get_settings()
_state: dict[str, Any] = {"tasks": set()}


@asynccontextmanager
async def lifespan(_: FastAPI):
    svc = Services(settings)
    gw_state = GatewayState(settings)
    _state.update(svc=svc, gw=gw_state, pipeline=Pipeline(settings, svc, gw_state), dashboard=Dashboard(settings, svc, gw_state))
    if not settings.api_keys:
        log.warning("GATEWAY_API_KEYS boş → AUTH KAPALI (yalnızca geliştirme için)")
    yield
    for t in list(_state["tasks"]):
        t.cancel()
    await svc.aclose()


app = FastAPI(title="uskoruma-gateway", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

require_auth = make_auth_dependency(settings)
router = APIRouter(dependencies=[Depends(require_auth)])


# ------------------------------------------------------------------------------ sağlık (auth'suz)
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "gateway", "auth": bool(settings.api_keys)}


# ------------------------------------------------------------------------------ pipeline
@router.post("/pipeline/run")
async def pipeline_run(req: PipelineRequest, wait: bool = Query(True, description="False: 202 + run_id, ilerlemeyi dashboard/state ile izle")):
    """Zinciri sırayla tetikler: drone bağlamı → detect → georef → tracks → pattern → assess."""
    # Olay akışında bölge, görüntü konumundan (mock-data /zones/assign) çözülür; eski akışta varsayılan bölge
    run = new_run(req, req.zone_id)
    _state["gw"].add_run(run)
    coro = _state["pipeline"].run(run, req)
    if not wait:
        task = asyncio.create_task(coro)
        _state["tasks"].add(task)
        task.add_done_callback(_state["tasks"].discard)
        return JSONResponse(status_code=202, content={"run_id": run["run_id"], "status": run["status"]})
    await coro
    return JSONResponse(status_code=200 if run["status"] == "succeeded" else 502, content=run)


@router.get("/pipeline/runs")
async def pipeline_runs(limit: int = Query(20, ge=1, le=50)) -> dict:
    runs = list(reversed(_state["gw"].runs.values()))[:limit]
    return {"runs": [_run_summary(r) for r in runs]}


@router.get("/pipeline/runs/{run_id}")
async def pipeline_run_get(run_id: str) -> dict:
    run = _state["gw"].runs.get(run_id)
    if run is None:
        raise HTTPException(404, "Koşu bulunamadı")
    return run


# ------------------------------------------------------------------------------ olay kataloğu + dashboard
@router.get("/events")
async def events() -> dict:
    """Veri setindeki görüntüler (olaylar): bölge, capture_time, bu oturumdaki son değerlendirme. UI olay seçicisi bunu kullanır."""
    try:
        return await _state["dashboard"].events()
    except ServiceError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/dashboard/state")
async def dashboard_state(zone_id: str | None = None) -> dict:
    """Harita ve panellerin ihtiyaç duyduğu tüm durumun agregasyonu (UI bunu poll eder)."""
    return await _state["dashboard"].state(zone_id)


# ------------------------------------------------------------------------------ görüntü + reverse proxy
@router.get("/images/{image_id}")
async def image(image_id: str) -> Response:
    try:
        r = await _state["svc"].raw("detection", "GET", f"/images/{image_id}")
    except ServiceError as exc:
        raise HTTPException(502, str(exc)) from exc
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Görüntü bulunamadı")
    return Response(content=r.content, media_type=r.headers.get("content-type", "application/octet-stream"), headers={"Cache-Control": "private, max-age=300"})


@router.api_route("/proxy/{service}/{path:path}", methods=["GET", "POST"])
async def proxy(service: str, path: str, request: Request) -> Response:
    """Alt servislere ham reverse-proxy (auth'lu). Örn. /proxy/core/vehicles, /proxy/mock/intel/ZONE-ALPHA."""
    svc: Services = _state["svc"]
    if service not in svc.urls:
        raise HTTPException(404, f"Bilinmeyen servis: {service} (geçerli: {', '.join(svc.urls)})")
    segments = [p for p in path.split("/") if p]
    if ".." in segments:
        raise HTTPException(400, "Geçersiz yol")
    if segments and segments[0] == "admin" and not settings.allow_admin_proxy:
        raise HTTPException(403, "Admin uçları proxy'den kapalı (ALLOW_ADMIN_PROXY=true ile açılır)")
    body = await request.body()
    kw: dict[str, Any] = {"params": dict(request.query_params), "content": body or None}
    if "content-type" in request.headers:
        kw["headers"] = {"content-type": request.headers["content-type"]}
    if service == "risk":
        kw["timeout"] = settings.risk_timeout_s  # LLM'li değerlendirme uzun sürebilir
    try:
        r = await svc.raw(service, request.method, "/" + "/".join(segments), **kw)
    except ServiceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


app.include_router(router)  # kök yol: /pipeline/run, /dashboard/state ...
app.include_router(router, prefix="/api")  # Ingress/UI: /api/pipeline/run ...


@app.get("/api/health", include_in_schema=False)
async def api_health() -> dict:
    return await health()
