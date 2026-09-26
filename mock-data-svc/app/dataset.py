"""Olay veri seti kataloğu: zones.json + field_reports.json (yalnızca loader çıktılarını tutar ve sorgular)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .geo import haversine_m
from .claims import parse_claim
from .loaders.field_reports import FieldReport, load_reports
from .loaders.zones import BaseInfo, DatasetError, Zone, fold, load_zones
from .timeutil import iso

log = logging.getLogger("mock-data-svc.dataset")


@dataclass
class Catalog:
    settings: Settings
    base: BaseInfo | None = None
    zones: list[Zone] = field(default_factory=list)
    reports: list[FieldReport] = field(default_factory=list)
    _mention: dict[str, "re.Pattern[str]"] = field(default_factory=dict)

    # ------------------------------------------------------------------ yükleme
    @classmethod
    def load(cls, settings: Settings) -> "Catalog":
        """DATA_DIR boşsa boş katalog (eski/demo akışı). DATA_DIR verilmişse KATI: `zones.json` ve `field_reports.json`
        var olmalı ve geçerli olmalı, aksi halde `DatasetError` (servis açılmaz)."""
        cat = cls(settings)
        if not settings.data_dir:
            return cat
        root = Path(settings.data_dir)
        if not root.is_dir():
            raise DatasetError(f"DATA_DIR bulunamadı: {root}")
        for name in ("zones.json", "field_reports.json"):
            if not (root / name).is_file():
                raise DatasetError(f"{root / name} yok (DATA_DIR verildiğinde zorunlu)")
        cat.base, cat.zones = load_zones(root / "zones.json")
        cat.reports = load_reports(root / "field_reports.json", settings.dataset_date)
        cat._mention = {z.zone_id: cat._name_pattern(z.name) for z in cat.zones}
        log.info("Veri seti: üs=%s, %d bölge, %d saha raporu", cat.base.name, len(cat.zones), len(cat.reports))
        return cat

    @staticmethod
    def _name_pattern(name: str) -> "re.Pattern[str]":
        """Bölge adının metinde geçme kalıbı: ASCII'ye katlanmış, kelime başı sınırlı, son sözcük ek alabilir
        ('Kuzey Yolu' -> 'kuzey yolunda', 'kuzey yolundaki' eşleşir; 'kuzeydogu yolu' içindeki 'dogu yolu' eşleşmez)."""
        toks = fold(name).split()
        stem = toks[-1][: max(2, len(toks[-1]) - 2)]
        return re.compile(r"\b" + r"\s+".join([re.escape(t) for t in toks[:-1]] + [re.escape(stem)]))

    def mentioned_zones(self, text: str) -> set[str]:
        t = fold(text)
        return {zid for zid, pat in self._mention.items() if pat.search(t)}

    # ------------------------------------------------------------------ sorgular
    def base_dict(self) -> dict[str, Any] | None:
        if self.base is None:
            return None
        return {
            "name": self.base.name,
            "lat": self.base.lat,
            "lon": self.base.lon,
            "radius_m": self.base.radius_m if self.base.radius_m is not None else self.settings.base_radius_m,
            "alert_radius_m": self.base.alert_radius_m if self.base.alert_radius_m is not None else self.settings.alert_radius_m,
            "source": "dataset",
        }

    def zone(self, zone_id: str) -> Zone | None:
        zid = zone_id.lower()
        return next((z for z in self.zones if z.zone_id == zid), None)

    @staticmethod
    def zone_dict(z: Zone, base: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "zone_id": z.zone_id,
            "name": z.name,
            "center": {"lat": z.lat, "lon": z.lon},
            "distance_from_base_m": z.distance_from_base_m,
            "bearing_from_base_deg": z.bearing_from_base_deg,
            "base": base,
        }

    def nearest_zone(self, lat: float, lon: float) -> tuple[Zone, float] | None:
        if not self.zones:
            return None
        best = min(self.zones, key=lambda z: haversine_m(lat, lon, z.lat, z.lon))
        return best, haversine_m(lat, lon, best.lat, best.lon)

    def reports_for(self, zone_id: str, center: tuple[float, float], as_of: float | None, radius_km: float) -> list[dict[str, Any]]:
        """`zone_id` bölgesindeki bir olay (`center` konumunda, `as_of` anında) için ilgili saha raporları.

        Raporlar bölgeye bağlı değildir; ilişki şöyle kurulur:
          1. Metinde koordinat varsa: koordinatın EN YAKIN BÖLGESİ olay bölgesiyle aynıysa (olayların bölgesi de aynı
             kuralla çözülür) veya olay konumuna `radius_km` içindeyse ilgilidir.
          2. Koordinat yok ama metin bir bölge yolunu anıyorsa ("Kuzey yolunda ..."): yalnızca anılan bölgeler için.
          3. Ne konum ne bölge adı: genel bilgi (ör. planlı tatbikat) — tüm olaylar için.
        `as_of` sonrasındaki raporlar ASLA döndürülmez (olay anında henüz bilinmeyen bilgi sızmasın).
        """
        zid = zone_id.lower()
        out: list[dict[str, Any]] = []
        for idx, r in enumerate(self.reports):
            if as_of is not None and r.ts > as_of:
                continue
            dist: float | None = None
            if r.lat is not None and r.lon is not None:
                dist = haversine_m(center[0], center[1], r.lat, r.lon)
                near = self.nearest_zone(r.lat, r.lon)
                if not ((near is not None and near[0].zone_id == zid) or dist <= radius_km * 1000):
                    continue
            else:
                mentioned = self.mentioned_zones(r.text)
                if mentioned and zid not in mentioned:
                    continue
            out.append(
                {
                    "id": f"r{idx:03d}",
                    "claim": parse_claim(r.text),  # yapılandırılmış iddia (tip/sayı/hareket/kimlik); doğrulaması core-svc'de
                    "text": r.text,
                    "reporter": r.source,
                    "source": r.source,
                    "ts": iso(r.ts),
                    "time": r.time,
                    "location": {"lat": r.lat, "lon": r.lon} if r.lat is not None else None,
                    "distance_m": round(dist, 0) if dist is not None else None,
                    "age_min": round((as_of - r.ts) / 60, 1) if as_of is not None else None,
                }
            )
        return out
