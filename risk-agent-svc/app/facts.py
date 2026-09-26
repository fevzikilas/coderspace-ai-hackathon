from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Facts:
    """Bir değerlendirme sırasında toplanan (araç çağrılarıyla dolan) olgular."""

    zone_id: str
    detection: dict[str, Any]  # core-svc /detections/{id} kaydı
    base: dict[str, float]  # lat, lon, radius_m
    movement: dict[str, dict[str, Any]] = field(default_factory=dict)  # vehicle_id -> core analyze sonucu
    pattern: dict[str, Any] | None = None  # pattern-svc sonucu (ham)
    intel: list[dict[str, Any]] | None = None
    reports: list[dict[str, Any]] | None = None
    drone: dict[str, Any] | None = None
    # Olay anı (capture_time, ISO). Tüm "yaş"/geçmiş hesapları buna göre; duvar saati kullanılmaz.
    reference_time: str | None = None
    # Olayın konumu (tespitlerin ağırlık merkezi): konumlu saha raporlarını süzmek için
    event_point: dict[str, float] | None = None

    @property
    def vehicle_ids(self) -> list[str]:
        return list(self.detection.get("vehicle_ids") or [])

    @property
    def drone_id(self) -> str | None:
        return self.detection.get("drone_id")

    def matched_patterns(self) -> list[str]:
        if not self.pattern:
            return []
        names = [m["pattern"] for m in (self.pattern.get("detail") or {}).get("matched_patterns", [])]
        if not names and self.pattern.get("pattern") and self.pattern["pattern"] != "RANDOM":
            names = [self.pattern["pattern"]]
        return names
