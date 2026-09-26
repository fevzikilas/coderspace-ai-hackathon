"""risk-agent-svc — LLM tool-calling orkestratörü (GLM), açıklanabilir risk değerlendirmesi."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .agent import DetectionNotFound, RiskAgent
from .budget import Budget, BudgetExceeded
from .config import get_settings
from .db import Persistence
from .glm_client import GLMClient
from .store import AssessmentStore
from .tools import UpstreamError, Upstreams

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("risk-agent")

settings = get_settings()
_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    up = Upstreams(settings)
    glm = GLMClient(settings) if settings.glm_api_key else None
    budget = Budget(settings, glm.key_info if glm and settings.llm_provider == "glm" else None)  # key/info yalnızca GLM gateway'inde var
    store = AssessmentStore(settings.cache_size)
    persistence = Persistence(settings.database_url)
    _state.update(agent=RiskAgent(settings, up, glm, budget, store, persistence), budget=budget, store=store, glm=glm)
    if glm is None:
        log.warning("LLM anahtarı tanımlı değil (LLM_PROVIDER=%s) → yalnızca kural tabanlı (rule-based) değerlendirme yapılacak", settings.llm_provider)
    else:
        log.info("LLM hazır: sağlayıcı=%s model=%s base=%s reasoning_effort=%s günlük tavan=%s", settings.llm_provider, settings.glm_model, settings.glm_base_url,
                 settings.glm_reasoning_effort if settings.send_reasoning_effort else "(gönderilmiyor)", settings.llm_max_requests_per_day or "yok")
        await budget.refresh(force=True)  # yalnızca GLM: açılışta gerçek harcamayı oku (okunamazsa uyarı, servis çalışır)
        log.info("GLM bütçesi: %s", {k: v for k, v in budget.status().items() if k in ("spend_usd", "remaining_usd", "total_budget_usd", "key_info_error")})
    if os.getenv("GLM_THINKING"):
        log.warning("GLM_THINKING artık desteklenmiyor ve yok sayıldı: gateway 'thinking' parametresini reddeder. Bunun yerine GLM_REASONING_EFFORT=low|high|max kullanın.")
    yield
    if glm is not None:
        await glm.aclose()
    await up.aclose()


app = FastAPI(title="risk-agent-svc", version="1.0.0", lifespan=lifespan)


class BaseLocation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    radius_m: float | None = Field(None, ge=0)


class AssessRequest(BaseModel):
    zone_id: str
    detection_id: str
    force: bool = Field(False, description="True: önbelleği yok say, yeniden değerlendir (bütçe harcar)")
    base_location: BaseLocation | None = Field(None, description="Korunan üs (gateway verir; yoksa BASE_* ortam varsayılanı). Değerlendirme anı detection kaydındaki reference_time'dan gelir")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "risk-agent-svc",
        "llm": settings.llm_provider if _state.get("glm") is not None else "disabled (rule-based)",
        "model": settings.glm_model if _state.get("glm") is not None else None,
    }


@app.post("/assess")
async def assess(req: AssessRequest) -> dict[str, Any]:
    """Tespit için gerekçeli risk alarmı üretir: risk_level, rationale, confidence, evidence_breakdown[], tool_calls_log[]."""
    try:
        return await _state["agent"].assess(req.zone_id, req.detection_id, force=req.force, base_location=req.base_location.model_dump() if req.base_location else None)
    except DetectionNotFound as exc:
        raise HTTPException(404, f"Bilinmeyen detection_id: {exc}") from exc
    except BudgetExceeded as exc:
        raise HTTPException(429, f"GLM bütçesi/kotası aşıldı: {exc}") from exc
    except UpstreamError as exc:
        raise HTTPException(502, f"Upstream hatası: {exc}") from exc


@app.get("/assessments")
def list_assessments(zone_id: str | None = None, limit: int = Query(10, ge=1, le=100)) -> dict:
    return {"assessments": _state["store"].list(zone_id, limit)}


@app.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: str) -> dict:
    a = _state["store"].get(assessment_id)
    if a is None:
        raise HTTPException(404, "Değerlendirme bulunamadı")
    return a


@app.get("/budget")
async def budget(refresh: bool = Query(False, description="true: key/info'yu şimdi oku (aksi halde TTL'li önbellek)")) -> dict:
    b = _state["budget"]
    await b.refresh(force=refresh)
    return b.status()
