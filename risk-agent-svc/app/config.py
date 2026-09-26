from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


_EFFORTS = ("low", "high", "max")
_OFFICIAL_BASE_URL = "https://berriailitellm-databasev1826rc3-production-d691.up.railway.app/v1"


def _effort() -> str:
    v = os.getenv("GLM_REASONING_EFFORT", "low").strip().lower()
    if v not in _EFFORTS:
        raise ValueError(f"GLM_REASONING_EFFORT={v!r} geçersiz; {'|'.join(_EFFORTS)} olmalı ('thinking' parametresi gateway'de hata verir, kullanılmaz)")
    return v


_PROVIDERS = ("glm", "openrouter")
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _provider() -> str:
    """Aktif LLM sağlayıcısı: `LLM_PROVIDER=glm` (varsayılan, organizatör gateway'i) | `openrouter` (geçici test)."""
    p = (os.getenv("LLM_PROVIDER") or "glm").strip().lower()
    if p not in _PROVIDERS:
        raise ValueError(f"LLM_PROVIDER={p!r} geçersiz; {'|'.join(_PROVIDERS)} olmalı")
    return p


def _base_url() -> str:
    if v := (os.getenv("LLM_BASE_URL") or "").strip():  # sağlayıcıdan bağımsız açık geçersiz kılma
        return v.rstrip("/")
    if _provider() == "openrouter":
        return (os.getenv("OPENROUTER_BASE_URL") or _OPENROUTER_BASE_URL).rstrip("/")
    return (os.getenv("GLM_BASE_URL") or _OFFICIAL_BASE_URL).rstrip("/")


def _model() -> str:
    if v := (os.getenv("LLM_MODEL") or "").strip():
        return v
    if _provider() == "openrouter":
        return os.getenv("OPENROUTER_MODEL") or _OPENROUTER_MODEL
    return os.getenv("GLM_MODEL") or "glm-5.3-flash"  # GLM'de tam olarak bu ad; başka ad "key not allowed" hatası verir


def _api_key() -> str | None:
    if v := (os.getenv("LLM_API_KEY") or "").strip():
        return v
    return (os.getenv("OPENROUTER_API_KEY") if _provider() == "openrouter" else os.getenv("GLM_API_KEY")) or None


def _by_provider(glm: int, other: int, env: str) -> int:
    v = os.getenv(env)
    return int(v) if v not in (None, "") else (glm if _provider() == "glm" else other)


def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None or not v.strip() else v.strip().lower() in {"1", "true", "yes", "on"}


def _root_url(base_url: str) -> str:
    """OpenAI uyumlu taban URL'den `/v1` ekini atar: `key/info` gibi yönetim uçları KÖK URL'dedir."""
    return base_url[:-3] if base_url.endswith("/v1") else base_url


@dataclass(frozen=True)
class Settings:
    # ---- LLM — OpenAI uyumlu chat/completions + tool calling. SAĞLAYICIDAN BAĞIMSIZ: LLM_PROVIDER=glm|openrouter seçer;
    # LLM_BASE_URL / LLM_MODEL / LLM_API_KEY sağlayıcı değerlerini geçersiz kılar. (Alan adları `glm_*` geriye uyumluluk için korunur.)
    llm_provider: str = field(default_factory=_provider)
    glm_api_key: str | None = field(default_factory=_api_key)
    # Sohbet uçları `{base}/chat/completions`. GLM: `/v1` ile biter.
    glm_base_url: str = field(default_factory=_base_url)
    # YALNIZCA GLM: bütçe sorgusu `{root}/key/info` — `/v1` OLMADAN kök URL. Ayrı tutulur; verilmezse base URL'den türetilir.
    glm_root_url: str = field(default_factory=lambda: (os.getenv("GLM_ROOT_URL") or _root_url(_base_url())).rstrip("/"))
    glm_model: str = field(default_factory=_model)
    glm_timeout_s: float = field(default_factory=lambda: _f("GLM_TIMEOUT_S", 120))  # model düşünür: yanıt birkaç saniye-dakika sürebilir
    glm_temperature: float | None = field(default_factory=lambda: _f("GLM_TEMPERATURE", 0.2) if os.getenv("GLM_TEMPERATURE", "0.2").strip() else None)  # boş = gönderme
    # low | high | max — YALNIZCA GLM'e gönderilir (bkz. send_reasoning_effort). `thinking` parametresi ASLA gönderilmez (GLM'de hata verir).
    glm_reasoning_effort: str = field(default_factory=_effort)
    # Diğer sağlayıcılar bu parametreyi tanımayabilir (hata verir): varsayılan yalnızca glm'de açık. LLM_SEND_REASONING_EFFORT=true|false ile değişir.
    send_reasoning_effort: bool = field(default_factory=lambda: _bool_env("LLM_SEND_REASONING_EFFORT", _provider() == "glm"))
    # max_tokens düşünmeyi de kapsar: düşükse content boş döner (finish_reason=length). İstemci en az 1000'e yükseltir.
    glm_max_tokens: int = field(default_factory=lambda: _i("GLM_MAX_TOKENS", 4000))
    glm_retries: int = field(default_factory=lambda: _i("GLM_RETRIES", 5))  # 429/5xx/ağ hatasında üstel bekleme ile
    glm_max_concurrent: int = field(default_factory=lambda: _by_provider(4, 2, "GLM_MAX_CONCURRENT"))  # GLM takım limiti: aynı anda en çok 4
    # Günlük istek tavanı (0 = yok). openrouter ücretsiz katman: 50/gün → varsayılan 40; aşılınca kural motoruna düşülür (yanıt üretmeye devam eder).
    llm_max_requests_per_day: int = field(default_factory=lambda: _by_provider(0, 40, "LLM_MAX_REQUESTS_PER_DAY"))

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
    max_rounds: int = field(default_factory=lambda: _i("AGENT_MAX_ROUNDS", 12))
    max_tool_calls: int = field(default_factory=lambda: _i("AGENT_MAX_TOOL_CALLS", 14))
    max_vehicles: int = field(default_factory=lambda: _i("AGENT_MAX_VEHICLES", 10))
    max_concurrent: int = field(default_factory=lambda: _i("AGENT_MAX_CONCURRENT", 3))
    result_log_chars: int = field(default_factory=lambda: _i("TOOL_LOG_RESULT_CHARS", 6000))

    # ---- Bütçe / kota (organizatör: TOPLAM 15 USD, yarışma boyunca SIFIRLANMAZ; 60 istek/dk; 500K token/dk; 4 eşzamanlı)
    # Gerçek harcama `GET {glm_root_url}/key/info` (spend / max_budget) ile okunur; yerel sayaçlar yalnızca ek güvenliktir.
    budget_total_usd: float = field(default_factory=lambda: _f("BUDGET_TOTAL_USD", 15.0))  # key/info max_budget vermezse kullanılır
    budget_reserve_usd: float = field(default_factory=lambda: _f("BUDGET_RESERVE_USD", 1.0))  # kalan bunun altına inerse LLM kullanılmaz
    budget_keyinfo_ttl_s: float = field(default_factory=lambda: _f("BUDGET_KEYINFO_TTL_S", 60))
    budget_requests_per_minute: int = field(default_factory=lambda: _by_provider(50, 15, "BUDGET_REQUESTS_PER_MINUTE"))  # GLM limiti 60, openrouter free 20: altında pay bırakır
    budget_tokens_per_minute: int = field(default_factory=lambda: _i("BUDGET_TOKENS_PER_MINUTE", 400_000))  # limit 500K
    budget_max_wait_s: float = field(default_factory=lambda: _f("BUDGET_MAX_WAIT_S", 20))  # dakikalık pencere dolarsa en çok bu kadar bekle, sonra fallback
    # Sonsuz döngüye giren ajana karşı: tek değerlendirmede toplam token (düşünme dahil) tavanı
    budget_tokens_per_assessment: int = field(default_factory=lambda: _i("BUDGET_TOKENS_PER_ASSESSMENT", 60_000))
    # fallback: kural motoruna düş | reject: HTTP 429
    on_budget_exceeded: str = field(default_factory=lambda: os.getenv("ON_BUDGET_EXCEEDED", "fallback").lower())

    cache_size: int = field(default_factory=lambda: _i("ASSESSMENT_CACHE_SIZE", 500))
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL") or None)


def get_settings() -> Settings:
    return Settings()
