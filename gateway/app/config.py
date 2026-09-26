from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    detection_url: str = field(default_factory=lambda: os.getenv("DETECTION_SVC_URL", "http://localhost:8001").rstrip("/"))
    core_url: str = field(default_factory=lambda: os.getenv("CORE_SVC_URL", "http://localhost:8002").rstrip("/"))
    pattern_url: str = field(default_factory=lambda: os.getenv("PATTERN_SVC_URL", "http://localhost:8003").rstrip("/"))
    mock_url: str = field(default_factory=lambda: os.getenv("MOCK_DATA_SVC_URL", "http://localhost:8004").rstrip("/"))
    risk_url: str = field(default_factory=lambda: os.getenv("RISK_AGENT_SVC_URL", "http://localhost:8005").rstrip("/"))

    # Tek auth katmanı. Boşsa auth KAPALI (yalnızca geliştirme!). Virgülle ayrılmış birden çok anahtar.
    api_keys: list[str] = field(default_factory=lambda: _list("GATEWAY_API_KEYS"))
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080"))
    # Admin uçlarının (örn. core-svc /admin/*) proxy üzerinden erişimi
    allow_admin_proxy: bool = field(default_factory=lambda: os.getenv("ALLOW_ADMIN_PROXY", "false").lower() == "true")

    # Üs ve varsayılan bölge
    base_name: str = field(default_factory=lambda: os.getenv("BASE_NAME", "Üs Alfa"))
    base_lat: float = field(default_factory=lambda: _f("BASE_LAT", 39.9000))
    base_lon: float = field(default_factory=lambda: _f("BASE_LON", 32.7500))
    base_radius_m: float = field(default_factory=lambda: _f("BASE_RADIUS_M", 1500))
    alert_radius_m: float = field(default_factory=lambda: _f("ALERT_RADIUS_M", 8000))
    default_zone_id: str = field(default_factory=lambda: os.getenv("DEFAULT_ZONE_ID", "ZONE-ALPHA"))

    http_timeout_s: float = field(default_factory=lambda: _f("HTTP_TIMEOUT_S", 15))
    risk_timeout_s: float = field(default_factory=lambda: _f("RISK_TIMEOUT_S", 120))
    pipeline_timeout_s: float = field(default_factory=lambda: _f("PIPELINE_TIMEOUT_S", 180))
    max_concurrent_runs: int = field(default_factory=lambda: int(_f("MAX_CONCURRENT_RUNS", 2)))
    max_runs: int = field(default_factory=lambda: int(_f("MAX_RUNS", 50)))
    max_logs: int = field(default_factory=lambda: int(_f("MAX_LOGS", 300)))

    # Dashboard önbellek süreleri (sn) — UI polling'i alt servisleri boğmasın
    ttl_fast_s: float = field(default_factory=lambda: _f("TTL_FAST_S", 1.0))
    ttl_slow_s: float = field(default_factory=lambda: _f("TTL_SLOW_S", 15.0))
    ttl_health_s: float = field(default_factory=lambda: _f("TTL_HEALTH_S", 5.0))


def get_settings() -> Settings:
    return Settings()
