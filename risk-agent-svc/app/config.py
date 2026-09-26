from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # ---- GLM (Zhipu / Z.ai) — OpenAI uyumlu chat/completions + tool calling
    glm_api_key: str | None = field(default_factory=lambda: os.getenv("GLM_API_KEY") or None)
    glm_base_url: str = field(default_factory=lambda: os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/"))
    glm_model: str = field(default_factory=lambda: os.getenv("GLM_MODEL", "glm-4.5"))
    glm_timeout_s: float = field(default_factory=lambda: _f("GLM_TIMEOUT_S", 60))
    glm_temperature: float = field(default_factory=lambda: _f("GLM_TEMPERATURE", 0.2))
    glm_max_tokens: int = field(default_factory=lambda: _i("GLM_MAX_TOKENS", 1800))
    glm_retries: int = field(default_factory=lambda: _i("GLM_RETRIES", 2))
    # "" (gönderme) | "enabled" | "disabled" — GLM-4.5+ 'thinking' parametresi
    glm_thinking: str = field(default_factory=lambda: os.getenv("GLM_THINKING", ""))

    # ---- Upstream servisler (yalnızca HTTP)
    core_svc_url: str = field(default_factory=lambda: os.getenv("CORE_SVC_URL", "http://localhost:8002").rstrip("/"))
    pattern_svc_url: str = field(default_factory=lambda: os.getenv("PATTERN_SVC_URL", "http://localhost:8003").rstrip("/"))
    mock_data_svc_url: str = field(default_factory=lambda: os.getenv("MOCK_DATA_SVC_URL", "http://localhost:8004").rstrip("/"))
    http_timeout_s: float = field(default_factory=lambda: _f("HTTP_TIMEOUT_S", 10))

    # ---- Üs
    base_lat: float = field(default_factory=lambda: _f("BASE_LAT", 39.9000))
    base_lon: float = field(default_factory=lambda: _f("BASE_LON", 32.7500))
    base_radius_m: float = field(default_factory=lambda: _f("BASE_RADIUS_M", 1500))

    # ---- Ajan döngüsü
    max_rounds: int = field(default_factory=lambda: _i("AGENT_MAX_ROUNDS", 8))
    max_tool_calls: int = field(default_factory=lambda: _i("AGENT_MAX_TOOL_CALLS", 14))
    max_vehicles: int = field(default_factory=lambda: _i("AGENT_MAX_VEHICLES", 10))
    max_concurrent: int = field(default_factory=lambda: _i("AGENT_MAX_CONCURRENT", 3))
    result_log_chars: int = field(default_factory=lambda: _i("TOOL_LOG_RESULT_CHARS", 6000))

    # ---- Bütçe / kota
    budget_daily_tokens: int = field(default_factory=lambda: _i("BUDGET_DAILY_TOKENS", 200_000))
    budget_requests_per_hour: int = field(default_factory=lambda: _i("BUDGET_REQUESTS_PER_HOUR", 120))
    budget_assessments_per_hour: int = field(default_factory=lambda: _i("BUDGET_ASSESSMENTS_PER_HOUR", 60))
    budget_tokens_per_assessment: int = field(default_factory=lambda: _i("BUDGET_TOKENS_PER_ASSESSMENT", 30_000))
    # fallback: kural motoruna düş | reject: HTTP 429
    on_budget_exceeded: str = field(default_factory=lambda: os.getenv("ON_BUDGET_EXCEEDED", "fallback").lower())

    cache_size: int = field(default_factory=lambda: _i("ASSESSMENT_CACHE_SIZE", 500))
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL") or None)


def get_settings() -> Settings:
    return Settings()
