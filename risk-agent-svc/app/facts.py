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
    # max_vehicles sınırı yüzünden analize ALINMAYAN izli araçlar (sessizce atlanmasın)
    dropped_vehicle_ids: list[str] = field(default_factory=list)
    # Raporların KENDİ verimizle nicel karşılaştırması (core-svc /verify-claims): özet sayaçlar + kısa maddeler; yoksa None
    report_verification: dict[str, Any] | None = None
    drone: dict[str, Any] | None = None
    # Olay anı (capture_time, ISO). Tüm "yaş"/geçmiş hesapları buna göre; duvar saati kullanılmaz.
    reference_time: str | None = None
    # Olayın konumu (tespitlerin ağırlık merkezi): konumlu saha raporlarını süzmek için
    event_point: dict[str, float] | None = None
    # Cross-event görsel benzerlik yalnızca aday kanıttır; kimlik veya risk truth layer değildir.
    vehicle_link_evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def vehicle_ids(self) -> list[str]:
        return list(self.detection.get("vehicle_ids") or [])

    def untracked(self) -> list[dict[str, Any]]:
        """İz kaydıyla eşleşmeyen tespitler: HAREKET VERİSİ YOK (park halindeki araç, izi olmayan araç ya da yanlış pozitif olabilir)."""
        return [d for d in self.detection.get("detections", []) if not d.get("vehicle_id")]

    def data_gaps(self) -> dict[str, Any] | None:
        """Risk hesabına KATILAMAYAN nesneler: açıkça raporlanır; 'hareket verisi yok' 'zararsız/LOW' demek değildir."""
        un = self.untracked()
        if not un and not self.dropped_vehicle_ids:
            return None
        classes: dict[str, int] = {}
        for d in un:
            classes[str(d.get("class", "?"))] = classes.get(str(d.get("class", "?")), 0) + 1
        parts = []
        if un:
            parts.append(f"{len(un)} nesne izsiz ({', '.join(f'{v} {k}' for k, v in sorted(classes.items()))}): hareket verisi YOK — risk kademesine katılmadı, LOW/zararsız varsayılmadı")
        if self.dropped_vehicle_ids:
            parts.append(f"{len(self.dropped_vehicle_ids)} izli araç analiz sınırı (AGENT_MAX_VEHICLES) nedeniyle değerlendirilmedi: {', '.join(self.dropped_vehicle_ids)}")
        return {
            "untracked_detections": len(un),
            "untracked_classes": classes,
            "untracked": [{"class": d.get("class"), "conf": d.get("conf"), "lat": d.get("lat"), "lon": d.get("lon")} for d in un[:40]],
            "vehicles_over_limit": list(self.dropped_vehicle_ids),
            "note": "; ".join(parts) + ".",
        }

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
