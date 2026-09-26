from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Varsayılan üs (tüm servislerde aynı env adları). Sınır yarıçapı = "üs sınırı".
    base_lat: float = field(default_factory=lambda: float(os.getenv("BASE_LAT", "39.9000")))
    base_lon: float = field(default_factory=lambda: float(os.getenv("BASE_LON", "32.7500")))
    base_radius_m: float = field(default_factory=lambda: float(os.getenv("BASE_RADIUS_M", "1500")))

    # DEMO_MODE: duvar saatine çapalı sentetik demo izleri (kafile + loitering + random) + /admin/demo/reset.
    # Gerçek/olay verisiyle çalışırken KAPALI olmalıdır (varsayılan). Eski SEED_DEMO adı geri uyumluluk için okunur.
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", _bool("SEED_DEMO", False)))
    enable_admin: bool = field(default_factory=lambda: _bool("ENABLE_ADMIN", True))

    # Olay (event snapshot) veri seti: <DATA_DIR>/image_meta.json + tracks.csv. Boşsa veri seti kapalı.
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", ""))
    # image_meta.capture_time ve tracks.csv `time` "HH:MM" biçiminde (tarihsiz) gelir; epoch'a çevirmek için tek bir
    # sabit tarih kullanılır (yalnızca sıralama/fark için; TÜM servislerde aynı olmalı).
    dataset_date: str = field(default_factory=lambda: os.getenv("DATASET_DATE", "2025-06-01"))
    # Tespit ↔ iz eşleştirme (olay anında): kapı (m) ve iz sonunu en fazla kaç sn ileri taşıyabileceğimiz
    # Tespit↔iz eşleştirme mesafe sınırı (resmî: "birkaç metrelik sapma"; PDF örneğinde ~2.5 m). Yalnızca time==capture_time satırları aday.
    match_gate_m: float = field(default_factory=lambda: float(os.getenv("MATCH_GATE_M", "15")))
    match_max_extrap_s: float = field(default_factory=lambda: float(os.getenv("MATCH_MAX_EXTRAP_S", "600")))

    # Hareket analizi
    # Analiz penceresi örnekleme aralığına uyarlanır: max(bu değer, ~2.5 x medyan adım) — 5 dk'lık izlerde ~12 dk.
    analysis_window_s: float = field(default_factory=lambda: float(os.getenv("ANALYSIS_WINDOW_S", "120")))
    approach_min_mps: float = field(default_factory=lambda: float(os.getenv("APPROACH_MIN_MPS", "0.8")))
    stationary_mps: float = field(default_factory=lambda: float(os.getenv("STATIONARY_MPS", "0.5")))
    stale_after_s: float = field(default_factory=lambda: float(os.getenv("STALE_AFTER_S", "900")))

    # Tracker (detection -> vehicle ilişkilendirme)
    gate_base_m: float = field(default_factory=lambda: float(os.getenv("GATE_BASE_M", "100")))
    gate_growth_mps: float = field(default_factory=lambda: float(os.getenv("GATE_GROWTH_MPS", "8")))
    gate_max_dt_s: float = field(default_factory=lambda: float(os.getenv("GATE_MAX_DT_S", "300")))
    retention_s: float = field(default_factory=lambda: float(os.getenv("RETENTION_S", str(6 * 3600))))
    max_points_per_vehicle: int = field(default_factory=lambda: int(os.getenv("MAX_POINTS_PER_VEHICLE", "6000")))
    max_events: int = field(default_factory=lambda: int(os.getenv("MAX_EVENTS", "500")))

    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL") or None)


def get_settings() -> Settings:
    return Settings()
