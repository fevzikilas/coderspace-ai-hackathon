from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    core_svc_url: str = field(default_factory=lambda: os.getenv("CORE_SVC_URL", "http://localhost:8002").rstrip("/"))
    http_timeout_s: float = field(default_factory=lambda: _f("HTTP_TIMEOUT_S", 10))
    max_vehicles: int = field(default_factory=lambda: int(os.getenv("MAX_VEHICLES", "50")))

    # Varsayılan üs (istekte base_location yoksa)
    base_lat: float = field(default_factory=lambda: _f("BASE_LAT", 39.9000))
    base_lon: float = field(default_factory=lambda: _f("BASE_LON", 32.7500))
    base_radius_m: float = field(default_factory=lambda: _f("BASE_RADIUS_M", 1500))

    # LOITERING: 2 saatte < 200 m yarıçap
    loiter_radius_m: float = field(default_factory=lambda: _f("LOITER_RADIUS_M", 200))
    loiter_window_s: float = field(default_factory=lambda: _f("LOITER_WINDOW_S", 7200))
    loiter_min_span_ratio: float = field(default_factory=lambda: _f("LOITER_MIN_SPAN_RATIO", 0.5))
    loiter_min_points: int = field(default_factory=lambda: int(_f("LOITER_MIN_POINTS", 10)))

    # DIRECT_APPROACH: mesafe monoton azalıyor, üs istikametinden sapma < 15°
    approach_window_s: float = field(default_factory=lambda: _f("APPROACH_WINDOW_S", 300))
    approach_min_points: int = field(default_factory=lambda: int(_f("APPROACH_MIN_POINTS", 5)))
    approach_max_dev_deg: float = field(default_factory=lambda: _f("APPROACH_MAX_DEV_DEG", 15))
    approach_noise_m: float = field(default_factory=lambda: _f("APPROACH_NOISE_M", 15))
    approach_min_closure_m: float = field(default_factory=lambda: _f("APPROACH_MIN_CLOSURE_M", 150))
    # Yön sapması yalnızca bu uzunluktan uzun adımlarla hesaplanır (durmuş aracın GPS gürültüsü yön sayılmasın).
    # Seyrek (5 dk) izlerde otomatik olarak en az approach_noise_m'e çıkar.
    approach_min_segment_m: float = field(default_factory=lambda: _f("APPROACH_MIN_SEGMENT_M", 5))

    # CONVOY: 2+ araç < 500 m, aynı yön, senkron hız
    convoy_dist_m: float = field(default_factory=lambda: _f("CONVOY_DIST_M", 500))
    convoy_heading_tol_deg: float = field(default_factory=lambda: _f("CONVOY_HEADING_TOL_DEG", 20))
    convoy_speed_ratio: float = field(default_factory=lambda: _f("CONVOY_SPEED_RATIO", 0.35))
    convoy_min_speed_mps: float = field(default_factory=lambda: _f("CONVOY_MIN_SPEED_MPS", 1.0))
    convoy_window_s: float = field(default_factory=lambda: _f("CONVOY_WINDOW_S", 300))
    convoy_sample_s: float = field(default_factory=lambda: _f("CONVOY_SAMPLE_S", 15))
    convoy_min_samples: int = field(default_factory=lambda: int(_f("CONVOY_MIN_SAMPLES", 5)))
    convoy_min_frac: float = field(default_factory=lambda: _f("CONVOY_MIN_FRAC", 0.8))
    convoy_max_gap_s: float = field(default_factory=lambda: _f("CONVOY_MAX_GAP_S", 120))


def get_settings() -> Settings:
    return Settings()
