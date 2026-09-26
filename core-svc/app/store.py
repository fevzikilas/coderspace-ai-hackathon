"""Bellek-içi araç/iz deposu + basit tracker (en yakın komşu, sabit hız tahmini)."""
from __future__ import annotations

import bisect
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .geo import from_enu, haversine_m, to_enu

Point = tuple[float, float, float]  # (ts_epoch, lat, lon)

NEW_VEHICLE_ID_START = 1000  # V-1000+: tracker'ın yarattığı araçlar; V-1xx/2xx/3xx demo seed


@dataclass
class Vehicle:
    id: str
    cls: str
    points: list[Point] = field(default_factory=list)  # ts'e göre sıralı
    demo: bool = False
    catalog: bool = False  # tracks.csv'den yüklendi (demo sıfırlamasında silinmez)

    @property
    def first_seen(self) -> float | None:
        return self.points[0][0] if self.points else None

    @property
    def last_seen(self) -> float | None:
        return self.points[-1][0] if self.points else None


class TrackStore:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._lock = threading.RLock()
        self._vehicles: dict[str, Vehicle] = {}
        self._events: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._next_id = NEW_VEHICLE_ID_START

    # ------------------------------------------------------------------ yönetim
    def reset(self) -> None:
        """Demo/ingest ile oluşan araçları ve olayları siler; veri seti (catalog) izleri korunur."""
        with self._lock:
            self._vehicles = {k: v for k, v in self._vehicles.items() if v.catalog}
            self._events.clear()
            self._next_id = NEW_VEHICLE_ID_START

    def put_vehicle(self, vehicle: Vehicle) -> None:
        with self._lock:
            vehicle.points.sort()
            self._vehicles[vehicle.id] = vehicle

    # ------------------------------------------------------------------ okuma
    def get(self, vehicle_id: str) -> Vehicle | None:
        with self._lock:
            return self._vehicles.get(vehicle_id)

    def list_vehicles(self) -> list[Vehicle]:
        with self._lock:
            return sorted(self._vehicles.values(), key=lambda v: v.id)

    def points(self, vehicle_id: str, window_s: float | None = None, until: float | None = None) -> list[Point] | None:
        """`until` (dahil) anına kadarki noktalar; `window_s` verilirse yalnızca son `window_s` saniyesi.

        Duvar saati KULLANILMAZ: `until` yoksa iz sonu referanstır. `until` = olayın referans zamanı (capture_time)
        olduğunda gelecekteki noktalar asla görünmez. Araç yoksa None.
        """
        with self._lock:
            v = self._vehicles.get(vehicle_id)
            if v is None:
                return None
            pts = v.points
            if until is not None:
                pts = pts[: bisect.bisect_right(pts, (until, 91.0, 181.0))]
            if window_s is not None and pts:
                ref = until if until is not None else pts[-1][0]
                pts = pts[bisect.bisect_left(pts, (ref - window_s, -91.0, -181.0)) :]
            return list(pts)

    def last_ts(self, until: float | None = None) -> float | None:
        """Depodaki (varsa `until`'e kadarki) en son nokta zamanı — varsayılan referans zaman."""
        with self._lock:
            best: float | None = None
            for v in self._vehicles.values():
                for p in reversed(v.points):
                    if until is None or p[0] <= until:
                        best = p[0] if best is None else max(best, p[0])
                        break
            return best

    # ------------------------------------------------------------------ yazma
    def add_point(self, vehicle_id: str, ts: float, lat: float, lon: float) -> bool:
        with self._lock:
            v = self._vehicles[vehicle_id]
            if any(abs(p[0] - ts) < 1e-6 for p in v.points[-5:]):
                return False  # aynı zaman damgası: tekrar
            bisect.insort(v.points, (ts, lat, lon))
            self._prune(v)
            return True

    def _prune(self, v: Vehicle) -> None:
        if len(v.points) > self._s.max_points_per_vehicle:
            del v.points[: len(v.points) - self._s.max_points_per_vehicle]
        cutoff = v.points[-1][0] - self._s.retention_s
        if v.points[0][0] < cutoff:
            idx = bisect.bisect_left(v.points, (cutoff, -91.0, -181.0))
            del v.points[:idx]

    def _new_id(self) -> str:
        vid = f"V-{self._next_id}"
        self._next_id += 1
        return vid

    def _predict(self, v: Vehicle, ts: float) -> tuple[float, float]:
        """Sabit hız modeliyle ts anındaki tahmini konum (tahmin süresi 60 sn ile sınırlı)."""
        t_last, lat_last, lon_last = v.points[-1]
        dt = max(0.0, ts - t_last)
        if dt == 0 or len(v.points) < 2:
            return lat_last, lon_last
        # Hız tahmini için son noktadan en az 1 sn (en çok 120 sn) önceki en yakın noktayı bul
        prev = next((p for p in reversed(v.points[-30:-1]) if t_last - p[0] >= 1.0), None)
        if prev is None or t_last - prev[0] > 120.0:
            return lat_last, lon_last
        e, n = to_enu(prev[1], prev[2], lat_last, lon_last)
        span = t_last - prev[0]
        horizon = min(dt, 60.0)
        return from_enu(lat_last, lon_last, e / span * horizon, n / span * horizon)

    def associate(self, dets: list[dict[str, Any]], ts: float) -> list[str]:
        """Georeferanslanmış tespitleri araçlara eşler; eşleşmeyene yeni araç açar.

        dets: [{"class", "lat", "lon"}]. Dönen liste dets ile aynı sırada araç ID'leridir.
        Eşleştirme: (tespit, araç) çiftleri mesafeye göre artan sıralanır, birebir açgözlü atama.
        Kapı = gate_base + gate_growth * min(dt, gate_max_dt).
        """
        with self._lock:
            pairs: list[tuple[float, int, str]] = []
            for i, d in enumerate(dets):
                for v in self._vehicles.values():
                    if not v.points:
                        continue
                    dt = max(0.0, ts - v.points[-1][0])
                    plat, plon = self._predict(v, ts)
                    dist = haversine_m(plat, plon, d["lat"], d["lon"])
                    gate = self._s.gate_base_m + self._s.gate_growth_mps * min(dt, self._s.gate_max_dt_s)
                    if dist <= gate:
                        pairs.append((dist, i, v.id))
            pairs.sort()
            assigned: dict[int, str] = {}
            used: set[str] = set()
            for _dist, i, vid in pairs:
                if i in assigned or vid in used:
                    continue
                assigned[i] = vid
                used.add(vid)

            out: list[str] = []
            for i, d in enumerate(dets):
                vid = assigned.get(i)
                if vid is None:
                    vid = self._new_id()
                    self._vehicles[vid] = Vehicle(id=vid, cls=str(d.get("class", "vehicle")))
                self.add_point(vid, ts, d["lat"], d["lon"])
                out.append(vid)
            return out

    # ------------------------------------------------------------------ tespit olayları
    def new_event_id(self) -> str:
        return "det-" + uuid.uuid4().hex[:12]

    def record_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events[event["detection_id"]] = event
            self._events.move_to_end(event["detection_id"])
            while len(self._events) > self._s.max_events:
                self._events.popitem(last=False)

    def get_event(self, detection_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._events.get(detection_id)

    def list_events(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._events.values()))[:limit]


def position_at(points: list[Point], ts: float, max_extrap_s: float) -> tuple[float, float] | None:
    """`ts` anındaki konum; YALNIZCA ts'e kadarki noktalar kullanılır (gelecek verisi sızmaz).

    ts iz başlangıcından önceyse None. Son nokta ts'ten eskiyse sabit hızla en çok `max_extrap_s` ileri taşınır.
    """
    idx = bisect.bisect_right(points, (ts, 91.0, 181.0)) - 1
    if idx < 0:
        return None
    t_i, lat_i, lon_i = points[idx]
    gap = ts - t_i
    if gap < 1.0:
        return lat_i, lon_i
    if gap > max_extrap_s:
        return None
    if idx == 0:
        return lat_i, lon_i
    t_p, lat_p, lon_p = points[idx - 1]
    e, n = to_enu(lat_p, lon_p, lat_i, lon_i)
    span = t_i - t_p
    return from_enu(lat_i, lon_i, e / span * gap, n / span * gap) if span > 0 else (lat_i, lon_i)


@dataclass(frozen=True)
class TrackMatch:
    vehicle_id: str
    distance_m: float
    method: str  # "exact": time == capture_time satırı (resmî yol) | "interpolated": ızgara dışı yedek yol


def row_at(points: list[Point], ts: float) -> Point | None:
    """`ts` anındaki TAM satır (|Δt| < 1 sn); yoksa None. Enterpolasyon/ekstrapolasyon YAPMAZ."""
    idx = bisect.bisect_left(points, (ts - 1.0,))
    if idx < len(points) and abs(points[idx][0] - ts) < 1.0:
        return points[idx]
    return None


def match_detections(store: "TrackStore", dets: list[tuple[float, float]], ts: float, gate_m: float, max_extrap_s: float) -> list[TrackMatch | None]:
    """Olay anındaki (georeferanslı) tespitleri tracks.csv izlerine eşler (resmî yöntem, İKİ AŞAMALI). Depoyu DEĞİŞTİRMEZ.

    1. Aşama — SÜZ: yalnızca `time == capture_time` olan satırlar aday olur. Bir izin başka bir andaki (yakın ama eşit
       olmayan) noktası, ya da capture_time'da satırı olmayan bir izin ekstrapole edilmiş "hayalet" konumu ASLA aday değildir.
    2. Aşama — EN YAKIN: adaylar tespite uzaklığa göre artan sıralanır, `gate_m` içindekiler birebir açgözlü atanır.
       Eşleşmeyen tespit None döner (izsiz nesne: park halindeki araç vb.) — bu bir hata değildir.

    Yedek yol: veri setinde capture_time'da HİÇBİR izin satırı yoksa (izler farklı bir zaman ızgarasında) eski davranış:
    `ts` anına kadarki noktalardan enterpolasyon/ekstrapolasyon (en çok `max_extrap_s`), gelecek verisi kullanılmaz.
    """
    with store._lock:
        cands: dict[str, tuple[float, float]] = {}
        for v in store._vehicles.values():
            row = row_at(v.points, ts)
            if row is not None:
                cands[v.id] = (row[1], row[2])
        method = "exact"
        if not cands:
            method = "interpolated"
            for v in store._vehicles.values():
                pos = position_at(v.points, ts, max_extrap_s)
                if pos is not None:
                    cands[v.id] = pos
    pairs: list[tuple[float, int, str]] = []
    for i, (lat, lon) in enumerate(dets):
        for vid, (plat, plon) in cands.items():
            d = haversine_m(plat, plon, lat, lon)
            if d <= gate_m:
                pairs.append((d, i, vid))
    pairs.sort()
    out: list[TrackMatch | None] = [None] * len(dets)
    used: set[str] = set()
    for d, i, vid in pairs:
        if out[i] is None and vid not in used:
            out[i] = TrackMatch(vid, d, method)
            used.add(vid)
    return out
