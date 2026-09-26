"""detection-svc — araç tespiti (ultralytics YOLO | D-FINE | mock). Model yüklenemezse sessizce mock'a düşmez."""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response

from .config import get_settings
from .dataset import find_image, media_type, safe_id
from .detector import Detector, build_detector
from .images import (
    ImageCache,
    ImageError,
    decode_b64,
    fetch_url,
    make_image_id,
    open_image,
)
from .schemas import Box, DetectRequest, DetectResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("detection-svc")

settings = get_settings()
_state: dict[str, object] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state["detector"] = build_detector(settings)
    _state["cache"] = ImageCache(settings.image_cache_size)
    det = _state["detector"]
    log.info("detection-svc hazır (mode=%s backend=%s%s)", det.mode, det.backend, f" DEGRADED: {det.fallback_reason}" if det.fallback_reason else "")  # type: ignore[attr-defined]
    yield


app = FastAPI(title="detection-svc", version="1.0.0", lifespan=lifespan)


def _detector() -> Detector:
    return _state["detector"]  # type: ignore[return-value]


def _cache() -> ImageCache:
    return _state["cache"]  # type: ignore[return-value]


@app.get("/health")
def health() -> dict:
    """`degraded`: model istendi (MOCK_MODE=false) ama yüklenemedi ve MOCK_FALLBACK=true ile mock'a düşüldü. HTTP 200 kalır
    (konteyner sağlık kontrolü servisi öldürmesin) ama durum/sebep açıkça raporlanır; gateway/UI bunu sarı gösterir."""
    det = _detector()
    reason = getattr(det, "fallback_reason", None)
    return {
        "status": "degraded" if reason else "ok",
        "service": "detection-svc",
        "mode": det.mode,
        "backend": det.backend,
        "model_loaded": det.mode == "model",
        "model_path": None if det.mode == "mock" and not reason else settings.model_path,
        "fallback_reason": reason,
        "data_dir": settings.data_dir or None,
    }


@app.post("/detect", response_model=DetectResponse, response_model_by_alias=True)
def detect(req: DetectRequest) -> DetectResponse:
    """Görüntüdeki araçları tespit eder. Senkron route: FastAPI thread pool'unda çalışır."""
    ts = req.timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    detector = _detector()

    raw: bytes | None = None
    img = None
    content_type = "application/octet-stream"
    from_dataset = False
    try:
        if req.image_b64:
            raw = decode_b64(req.image_b64, settings.max_image_bytes)
        elif req.image_url:
            raw = fetch_url(req.image_url, settings)
        elif req.image_id:
            if not safe_id(req.image_id):
                raise ImageError("Geçersiz image_id")
            path = find_image(settings.data_dir, req.image_id)
            if path is not None:
                if path.stat().st_size > settings.max_image_bytes:
                    raise ImageError(f"Görüntü çok büyük (>{settings.max_image_bytes} bayt)")
                raw, from_dataset = path.read_bytes(), True
            elif detector.mode != "mock":
                raise ImageError(f"Veri setinde görüntü yok: {req.image_id}")
        elif detector.mode != "mock":
            raise ImageError("image_b64, image_url veya image_id zorunlu (model modu)")
        if raw is not None:
            img, content_type = open_image(raw)
    except ImageError as exc:
        raise HTTPException(400, str(exc)) from exc

    image_id = req.image_id or make_image_id(raw, req.drone_id or "-", ts)
    t0 = time.perf_counter()
    try:
        dets, w, h = detector.detect(img, req.drone_id, seed_key=image_id if raw is not None else "", image_id=req.image_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    elapsed = (time.perf_counter() - t0) * 1000

    if raw is not None and not from_dataset:
        # Kullanıcının yüklediği baytlar saklanır; UI kutuları bu görüntünün üstüne çizer. (Veri seti görüntüsü dosyadan servis edilir.)
        _cache().put(image_id, raw, content_type)

    log.info("detect drone=%s image=%s mode=%s boxes=%d (%.0f ms)", req.drone_id, image_id, detector.mode, len(dets), elapsed)
    return DetectResponse(
        image_id=image_id,
        drone_id=req.drone_id,
        timestamp=ts,
        boxes=[Box(cls=d.cls, conf=d.conf, x1=d.x1, y1=d.y1, x2=d.x2, y2=d.y2) for d in dets],
        image_width=w,
        image_height=h,
        mode=detector.mode,
        backend=detector.backend,
        fallback_reason=detector.fallback_reason,
        inference_ms=round(elapsed, 1),
    )


@app.get("/images/{image_id}")
def get_image(image_id: str) -> Response:
    item = _cache().get(image_id)
    if item is not None:
        raw, content_type = item
        return Response(content=raw, media_type=content_type, headers={"Cache-Control": "private, max-age=300"})
    path = find_image(settings.data_dir, image_id)
    if path is None:
        raise HTTPException(404, "Görüntü önbellekte ve veri setinde yok")
    return Response(content=path.read_bytes(), media_type=media_type(path), headers={"Cache-Control": "private, max-age=3600"})
