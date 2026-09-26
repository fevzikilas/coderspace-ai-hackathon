from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # Olay veri seti klasörü: zones.json + field_reports.json. Boşsa yalnızca eski (demo) mock veri sunulur.
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", ""))
    # 'HH:MM' -> epoch için sabit tarih (core-svc ile AYNI olmalı)
    dataset_date: str = field(default_factory=lambda: os.getenv("DATASET_DATE", "2025-06-01"))
    # zones.json base'inde yarıçap yoksa kullanılacak varsayılanlar
    base_radius_m: float = field(default_factory=lambda: float(os.getenv("BASE_RADIUS_M", "1500")))
    alert_radius_m: float = field(default_factory=lambda: float(os.getenv("ALERT_RADIUS_M", "8000")))
    # Konumu belli saha raporlarının bir noktaya (olay/bölge) uzaklık eşiği
    report_radius_km: float = field(default_factory=lambda: float(os.getenv("REPORT_RADIUS_KM", "3")))


def get_settings() -> Settings:
    return Settings()
