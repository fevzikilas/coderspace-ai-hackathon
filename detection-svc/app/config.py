from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # MOCK_MODE=true  -> her zaman mock bbox üretir (model gerekmez); bu bilinçli bir seçimdir, "degraded" sayılmaz
    # MOCK_MODE=false -> gerçek modeli yükler. Yüklenemezse servis AÇILMAZ (varsayılan). Yalnızca MOCK_FALLBACK=true
    #                    açıkça verilirse mock'a düşer; bu durumda /health "degraded" + fallback_reason raporlar.
    mock_mode: bool = field(default_factory=lambda: _bool("MOCK_MODE", True))
    mock_fallback: bool = field(default_factory=lambda: _bool("MOCK_FALLBACK", False))
    # ultralytics: D-fine .pt (Stage 1) | dfine: resmî D-FINE (Peterande/D-FINE) eğitim checkpoint'i
    model_backend: str = field(default_factory=lambda: os.getenv("MODEL_BACKEND", "ultralytics").strip().lower())
    model_path: str = field(default_factory=lambda: os.getenv("MODEL_PATH", "models/stage1.pt"))
    # dfine backend: resmî repo checkout'u (PYTHONPATH'e eklenir) ve onun yapılandırma dosyası
    dfine_repo: str = field(default_factory=lambda: os.getenv("DFINE_REPO", "/opt/D-FINE"))
    dfine_config: str = field(default_factory=lambda: os.getenv("DFINE_CONFIG", "configs/dfine/custom/dfine_hgnetv2_l_custom.yml"))
    dfine_use_ema: bool = field(default_factory=lambda: _bool("DFINE_USE_EMA", True))
    # Modelin sınıf sıralaması (id 0..N-1). D-FINE için eğitimdeki sıra: car,van,truck,bus
    class_names: list[str] = field(default_factory=lambda: _list("CLASS_NAMES", "car,van,truck,bus"))
    # Varsayılan 0.4 (gerçek 40 görüntüde ölçüldü: 0.25'e göre izsiz gürültü kutuları 219→89, izli araç recall %99→%96). Daha yüksek recall için CONF_THRESHOLD=0.25.
    conf_threshold: float = field(default_factory=lambda: float(os.getenv("CONF_THRESHOLD", "0.4")))
    max_detections: int = field(default_factory=lambda: int(os.getenv("MAX_DETECTIONS", "300")))
    iou_threshold: float = field(default_factory=lambda: float(os.getenv("IOU_THRESHOLD", "0.45")))
    img_size: int = field(default_factory=lambda: int(os.getenv("IMG_SIZE", "1280")))
    # Boş = modelin tüm sınıfları. Örn: "car,truck,pickup"
    vehicle_classes: list[str] = field(default_factory=lambda: _list("VEHICLE_CLASSES"))
    # Mock modda üretilecek sınıf adları
    mock_classes: list[str] = field(
        default_factory=lambda: _list("MOCK_CLASSES", "car,pickup,truck,van,bus,armored_vehicle")
    )
    # Olay veri seti: <DATA_DIR>/images/<image_id>.(jpg|jpeg|png) ve <DATA_DIR>/ground_truth.json (mock için gerçek bbox'lar)
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", ""))
    max_image_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_IMAGE_BYTES", str(15 * 1024 * 1024))))
    image_cache_size: int = field(default_factory=lambda: int(os.getenv("IMAGE_CACHE_SIZE", "16")))
    fetch_timeout_s: float = field(default_factory=lambda: float(os.getenv("FETCH_TIMEOUT_S", "10")))
    # image_url güvenliği (SSRF): boş allowlist = serbest, ama özel/loopback IP'ler yine yasak
    image_url_allowlist: list[str] = field(default_factory=lambda: _list("IMAGE_URL_ALLOWLIST"))
    allow_private_image_urls: bool = field(default_factory=lambda: _bool("ALLOW_PRIVATE_IMAGE_URLS", False))


def get_settings() -> Settings:
    return Settings()
