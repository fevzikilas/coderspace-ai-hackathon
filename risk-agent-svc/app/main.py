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
    budget = Budget(settings)
    store = AssessmentStore(settings.cache_size)
    persistence = Persistence(settings.database_url)
    _state.update(agent=RiskAgent(settings, up, glm, budget, store, persistence), budget=budget, store=store, glm=glm)
    if glm is None:
        log.warning("GLM_API_KEY tanımlı değil → yalnızca kural tabanlı (rule-based) değerlendirme yapılacak")
    else:
        log.info("GLM hazır: model=%s base=%s", settings.glm_model, settings.glm_base_url)
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
        "llm": "glm" if _state.get("glm") is not None else "disabled (rule-based)",
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
def budget() -> dict:
    return _state["budget"].status()
