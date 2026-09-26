"""Saha raporu iddialarının KENDİ verimizle NİCELİKSEL karşılaştırması (resmî görev tanımı: "Raporun iddiasını (tip, hareket, sayı)
kendi bulgularınızla karşılaştırın; çelişki varsa raporu değil tespitinizi esas alın").

REFERANS AN = görüntünün çekim anı (olay anı). Resmî örnekte 12:40 tarihli rapor, 13:25'teki tespitle karşılaştırılır: rapor konumu aracın
GÖRÜNTÜDEKİ konumudur, rapor saati yalnızca "önceden bildirildi" demektir. Gerçek veride doğrulandı: 72 koordinatlı raporun 71'i rapor SONRASI
çekimlerdeki bir D-FINE tespitine ≤70 m (medyan 3 m); rapor saatindeki iz konumuna ise 20'si ≥200 m uzakta (araç o sırada başka yerdeydi).
Bir rapor YALNIZCA konumu bu görüntünün ayak izinin İÇİNDEYSE bu görüntüdeki aracı anlatır (dışındakiler `irrelevant`: aynı rapor kendi görüntüsünde değerlendirilir).
Bu yüzden: konum/tip/sayı ← çekim anındaki tespitler + o andaki iz satırları; hareket ← aracın çekim anında biten izi (geçmişiyle);
rapor saati yalnızca ESKİLİK denetimi için (2 saatlik iz penceresinden eski rapor bu görüntüyü anlatamaz → doğrulanamadı).

Girdi: mock-data-svc'nin çıkardığı yapılandırılmış iddia (`claim`, bkz. mock-data-svc/app/claims.py) + rapor zamanı/konumu.
Gelecek verisi kullanılmaz (rapor zamanı ≤ çekim anı, iz ≤ çekim anı).

Her kontrol {aspect, claimed, observed, result: match|mismatch|unverifiable} üretir; verdict:
  incompatible  — en az bir kontrol UYUMSUZ
  compatible    — uyumsuz yok, en az bir kontrol UYUMLU (doğrulanamayan yönler `unverified` listesinde)
  unverifiable  — hiçbir yön doğrulanamadı
  irrelevant    — doğrulanabilir araç iddiası yok (hava, telsiz, plan, geçmiş ihbar …)
Kimlik ("dost", "kimlik teyidi yapılmıştır") ve renk kendi verimizle DOĞRULANAMAZ; verdict'i yalnızca tarif edilen araç/hareket belirler
ve kimlik iddiası hiçbir zaman riski düşürmez (politika).
"""
from __future__ import annotations

import math
from typing import Any

from .analysis import Base, analyze_points
from .config import Settings
from .geo import haversine_m
from .store import Point, TrackStore, position_at
from .timeutil import iso

# Eşikler (gerçek veride ölçüldü: rapor konumlarının %98'i çekim anındaki tespite/iz ucuna ≤70 m, medyan 3 m)
R_NEAR_M = 100.0     # "iddia konumunda araç vardı"
R_MAYBE_M = 250.0    # bu aralık: marjinal → doğrulanamaz
R_COUNT_M = 100.0    # sayım yarıçapı
R_DENSITY_M = 300.0  # "olağandan yoğun" yarıçapı
R_STATIONARY_M = 60.0  # duruyor: pencere boyunca rapor konumundan en çok bu kadar uzaklaşma (GPS gürültüsü payı)
FOOTPRINT_MARGIN_M = 20.0
ZONE_CLAIM_MAX_AGE_MIN = 30.0  # bölge düzeyi iddialar yalnızca bu kadar yeni raporlar için olay anıyla karşılaştırılır
FRESH_REPORT_MIN = 30.0         # konumda araç YOKLUĞU yalnızca bu kadar taze raporlar için çelişki sayılır (araç aradan geçen sürede gitmiş olabilir)
MAX_REPORT_AGE_MIN = 150.0    # 2 saatlik iz penceresi (+ pay): daha eski rapor bu görüntüdeki aracı anlatamaz
DEFAULT_WINDOW_MIN = 10.0
MAX_EXTRAP_S = 300.0
HEAVY = {"truck", "bus"}
MIN_DET_CONF = 0.4  # izsiz tespitleri sayarken alt güven (gerçek veride 0.25–0.4 arası izsiz kutuların çoğu gürültü)


def _check(aspect: str, claimed: Any, observed: Any, result: str) -> dict[str, Any]:
    return {"aspect": aspect, "claimed": claimed, "observed": observed, "result": result}


def _upto(points: list[Point], ts: float) -> list[Point]:
    return [p for p in points if p[0] <= ts + 1.0]


def _in_footprint(event: dict[str, Any], lat: float, lon: float) -> bool:
    """Görüntü ayak izi (resmî formülün eksenleri: enlem sol kenardan, boylam üst kenardan) + küçük pay."""
    img = event.get("image") or {}
    c = img.get("corner_coordinates")
    if not c:
        return False
    tl, tr, bl = c["top_left"], c["top_right"], c["bottom_left"]
    m_lat = FOOTPRINT_MARGIN_M / 111320.0
    m_lon = FOOTPRINT_MARGIN_M / (111320.0 * math.cos(math.radians(tl[0])))
    lat_lo, lat_hi = min(tl[0], bl[0]) - m_lat, max(tl[0], bl[0]) + m_lat
    lon_lo, lon_hi = min(tl[1], tr[1]) - m_lon, max(tl[1], tr[1]) + m_lon
    return lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi


def _verdict(checks: list[dict[str, Any]]) -> str:
    res = [c["result"] for c in checks]
    if "mismatch" in res:
        return "incompatible"
    if "match" in res:
        return "compatible"
    return "unverifiable"


def _summary(verdict: str, checks: list[dict[str, Any]], extra: str = "") -> str:
    parts = []
    for c in checks:
        mark = {"match": "✓", "mismatch": "✗", "unverifiable": "?"}[c["result"]]
        parts.append(f"{mark} {c['aspect']}: rapor {c['claimed']} / bulgu {c['observed']}")
    label = {"compatible": "UYUMLU", "incompatible": "UYUMSUZ", "unverifiable": "DOĞRULANAMADI", "irrelevant": "İLGİSİZ"}[verdict]
    return f"{label}: " + "; ".join(parts) + (f" {extra}" if extra else "")


class Verifier:
    def __init__(self, store: TrackStore, cfg: Settings, event: dict[str, Any], base: Base) -> None:
        self.store, self.cfg, self.event, self.base = store, cfg, event, base
        self.ref: float = float(event["ts"])
        self.dets: list[dict[str, Any]] = event.get("detections") or []
        self.cls_of: dict[str, str] = {d["vehicle_id"]: d["class"] for d in self.dets if d.get("vehicle_id")}
        self._vehicles = self.store.list_vehicles()

    # ------------------------------------------------------------------ iz yardımcıları
    def _tracks_near(self, lat: float, lon: float) -> list[tuple[float, str]]:
        """ÇEKİM ANINDA (ref) iddia konumuna yakın izler [(mesafe, vehicle_id)], yakından uzağa (bu görüntünün araçları dahil, tek havuz)."""
        out: list[tuple[float, str]] = []
        for v in self._vehicles:
            pos = position_at(_upto(v.points, self.ref), self.ref, MAX_EXTRAP_S)
            if pos is None:
                continue
            d = haversine_m(pos[0], pos[1], lat, lon)
            if d <= max(R_MAYBE_M, R_DENSITY_M):
                out.append((d, v.id))
        return sorted(out)

    def _points_upto(self, vid: str, ts: float) -> list[Point]:
        v = self.store.get(vid)
        return _upto(v.points, min(ts, self.ref)) if v else []

    def _motion_check(self, claim: dict[str, Any], vid: str) -> dict[str, Any]:
        """Aracın ÇEKİM ANINDA biten izinden hareket; iddia 'duruyor' ise pencere iddia edilen süre kadar geriye bakar."""
        pts = self._points_upto(vid, self.ref)
        motion = claim["motion"]
        if len(pts) < 2:
            return _check("hareket", motion, "iz geçmişi yetersiz", "unverifiable")
        if motion == "stationary":
            window_s = 60.0 * (claim.get("min_duration_min") or DEFAULT_WINDOW_MIN)
            last = pts[-1]
            win = [p for p in pts if last[0] - p[0] <= window_s]
            span_min = (win[-1][0] - win[0][0]) / 60.0
            far = max(haversine_m(p[1], p[2], last[1], last[2]) for p in win)
            need = window_s / 60.0
            claimed = f"≥{need:.0f} dk duruyor"
            if far > R_STATIONARY_M:
                return _check("hareket", claimed, f"son {span_min:.0f} dk içinde {far:.0f} m yer değiştirdi", "mismatch")
            if span_min + 1e-6 < need - 5.0:
                return _check("hareket", claimed, f"iz yalnızca {span_min:.0f} dk geriye gidiyor; bu sürede {far:.0f} m", "unverifiable")
            return _check("hareket", claimed, f"son {span_min:.0f} dk içinde en çok {far:.0f} m", "match")
        a = analyze_points(pts[-200:], self.base, self.cfg, self.ref)
        sp, cl = a["speed_mps"], a["closing_speed_mps"]
        obs = f"{sp:.1f} m/s, üsse yaklaşma {cl:+.1f} m/s"
        if motion == "toward_base":
            return _check("hareket", "üsse doğru ilerliyor", obs, "match" if a["approaching"] else "mismatch")
        if motion == "away":
            return _check("hareket", "üsten uzaklaşıyor", obs, "match" if cl <= -self.cfg.approach_min_mps else "mismatch")
        if motion == "moving":
            return _check("hareket", "hareket halinde", obs, "match" if sp >= self.cfg.stationary_mps else "mismatch")
        return _check("hareket", motion, obs, "unverifiable")

    # ------------------------------------------------------------------ iddia türleri
    def verify(self, item: dict[str, Any]) -> dict[str, Any]:
        claim = item["claim"]
        kind = claim.get("kind")
        t_c = float(item["ts"])
        if kind == "context":
            return {"verdict": "irrelevant", "checks": [], "unverified": [], "summary": f"İLGİSİZ: {claim.get('context_reason') or 'doğrulanabilir araç iddiası yok'}"}
        if kind in ("zone_no_heavy", "zone_activity"):
            return self._verify_zone(claim, t_c)
        return self._verify_vehicle(item, claim, t_c)

    def _verify_zone(self, claim: dict[str, Any], t_c: float) -> dict[str, Any]:
        age_min = (self.ref - t_c) / 60.0
        if age_min > ZONE_CLAIM_MAX_AGE_MIN:
            chk = [_check("bölge durumu", claim["kind"], f"rapor {age_min:.0f} dk eski; olay anındaki durumu kapsamaz", "unverifiable")]
            return {"verdict": "unverifiable", "checks": chk, "unverified": ["bölge durumu"], "summary": _summary("unverifiable", chk)}
        if claim["kind"] == "zone_no_heavy":
            heavy = [d for d in self.dets if d["class"] in HEAVY]
            if not self.dets:
                chk = [_check("ağır araç", "yok", "görüntüde tespit yok", "unverifiable")]
            elif heavy:
                chk = [_check("ağır araç", "yok, yalnızca binek araç", f"{len(heavy)} ağır araç tespit edildi ({', '.join(sorted({d['class'] for d in heavy}))})", "mismatch")]
            else:
                chk = [_check("ağır araç", "yok, yalnızca binek araç", f"{len(self.dets)} tespit, hepsi binek/van ({', '.join(sorted({d['class'] for d in self.dets}))})", "match")]
        else:  # zone_activity: olay anında üsse yaklaşan araç var mı?
            approaching, min_eta = 0, None
            for vid in sorted({d["vehicle_id"] for d in self.dets if d.get("vehicle_id")}):
                a = analyze_points(self._points_upto(vid, self.ref)[-200:], self.base, self.cfg, self.ref)
                if a["approaching"]:
                    approaching += 1
                    if a["eta_min"] is not None:
                        min_eta = a["eta_min"] if min_eta is None else min(min_eta, a["eta_min"])
            obs = "üsse yaklaşan araç yok" if not approaching else f"{approaching} araç üsse yaklaşıyor" + (f" (en yakın ETA {min_eta:.1f} dk)" if min_eta is not None else "")
            chk = [_check("bölge sakinliği", "olağan/sakin", obs, "match" if approaching == 0 else "mismatch")]
        v = _verdict(chk)
        return {"verdict": v, "checks": chk, "unverified": [], "summary": _summary(v, chk)}

    def _verify_vehicle(self, item: dict[str, Any], claim: dict[str, Any], t_c: float) -> dict[str, Any]:
        lat, lon = item.get("lat"), item.get("lon")
        if lat is None or lon is None:
            chk = [_check("konum", "koordinat", "raporda konum yok", "unverifiable")]
            return {"verdict": "unverifiable", "checks": chk, "unverified": ["konum"], "summary": _summary("unverifiable", chk)}
        age_min = (self.ref - t_c) / 60.0
        in_fp = _in_footprint(self.event, lat, lon)
        if not in_fp:
            # Bir rapor YALNIZCA konumu bu görüntünün ayak izi İÇİNDEYSE bu görüntüdeki aracı anlatır. Dışındaysa (yakında bir iz olsa da) başka bir
            # çekimdeki/bölgedeki aracı anlatır; aynı rapor kendi görüntüsünde değerlendirilir (tutarlı atıf).
            why = "rapor konumu bu görüntünün ayak izi dışında: başka bir çekimdeki/bölgedeki aracı anlatıyor"
            return {"verdict": "irrelevant", "checks": [], "unverified": [], "summary": f"İLGİSİZ: {why}", "report_time": iso(t_c), "in_image_footprint": False}
        if age_min > MAX_REPORT_AGE_MIN:
            chk = [_check("zaman", "rapor", f"{age_min:.0f} dk eski: 2 saatlik iz penceresinin dışında, bu görüntüdeki aracı anlatamaz", "unverifiable")]
            return {"verdict": "unverifiable", "checks": chk, "unverified": ["zaman"], "summary": _summary("unverifiable", chk), "report_time": iso(t_c)}
        types = claim.get("types") or []
        where = f"({lat:.4f}, {lon:.4f})"

        # Çekim anındaki gözlem: (a) o andaki iz satırları, (b) görüntüdeki tespitler (yalnızca ayak izi içindeyse anlamlı)
        trk = self._tracks_near(lat, lon)
        dets = [(haversine_m(d["lat"], d["lon"], lat, lon), d) for d in self.dets if d.get("conf", 1.0) >= MIN_DET_CONF]
        dets_near = sorted([x for x in dets if x[0] <= R_MAYBE_M], key=lambda x: x[0])
        nearest_trk = trk[0] if trk else (None, None)
        nearest_det = dets_near[0] if dets_near else (None, None)
        nearest_d = min([x for x in (nearest_trk[0], nearest_det[0]) if x is not None], default=None)
        if nearest_d is not None and nearest_d > R_MAYBE_M:  # 250 m'nin ötesi 'yakın araç yok' demektir (aday listesi yoğunluk için 300 m'ye kadar tutulur)
            nearest_d = None
        checks: list[dict[str, Any]] = []

        # ---- konum (çekim anında iddia konumunda araç var mı?) — konum görüntünün İÇİNDE: tüm araçlar (izli+izsiz) tespitlerde görünür
        if nearest_d is not None and nearest_d <= R_NEAR_M:
            what = f"iz {nearest_trk[1]} {nearest_trk[0]:.0f} m" if nearest_trk[0] is not None and nearest_trk[0] <= R_NEAR_M else f"tespit {nearest_det[1]['class']} {nearest_det[0]:.0f} m"
            checks.append(_check("konum", f"araç {where}", f"çekim anında {what}'de", "match"))
        elif nearest_d is not None:
            checks.append(_check("konum", f"araç {where}", f"çekim anında en yakın araç {nearest_d:.0f} m (marjinal)", "unverifiable"))
        elif age_min <= FRESH_REPORT_MIN:
            checks.append(_check("konum", f"araç {where}", f"çekim anında görüntüde de izlerde de o konumda araç yok (rapor {age_min:.0f} dk önce)", "mismatch"))
        else:
            checks.append(_check("konum", f"araç {where}", f"çekim anında o konumda araç yok; rapor {age_min:.0f} dk eski, aracın gitmiş olması mümkün", "unverifiable"))
        loc_ok = checks[0]["result"] == "match"

        # ---- tip: yakındaki araçların sınıfları (tespit; iz→tespit eşlemesi dahil)
        near_dets = [d for dist, d in dets if dist <= R_COUNT_M]
        known = {d["class"] for d in near_dets}
        if types:
            if not known:
                checks.append(_check("tip", "/".join(types), "yakındaki araçların sınıfı bilinmiyor (görüntüde tespit yok)", "unverifiable"))
            else:
                checks.append(_check("tip", "/".join(types), "/".join(sorted(known)), "match" if known & set(types) else "mismatch"))

        # ---- sayı (çekim anındaki araçlar). Görüntü ayak izi içindeyse tüm araçlar (izli+izsiz) gözlenir; dışındaysa yalnızca izliler.
        n = claim.get("count")
        tracked_ids = {vid for d, vid in trk if d <= R_COUNT_M}
        if claim.get("count_relation") == "exact" and n is not None:
            det_tracked = {d["vehicle_id"] for d in near_dets if d.get("vehicle_id")}
            match_known = [d for d in near_dets if not types or d["class"] in types]
            unknown_tracks = [vid for vid in tracked_ids if vid not in det_tracked]  # izli ama bu görüntüde tespit edilemedi/dışında: sınıfı bilinmiyor
            lo = len(match_known)
            hi = lo + len(unknown_tracks)
            obs = f"{lo}" if lo == hi else f"{lo}–{hi}"
            obs += f" araç ({R_COUNT_M:.0f} m içinde, görüntü+iz)"
            if not loc_ok:
                checks.append(_check("sayı", f"{n} araç", "konum doğrulanamadığı için sayılamadı", "unverifiable"))
            elif lo <= n <= hi:
                checks.append(_check("sayı", f"{n} araç", obs, "match"))
            elif n < lo:
                checks.append(_check("sayı", f"{n} araç", obs + " — iddiadan fazla", "mismatch"))
            else:
                checks.append(_check("sayı", f"{n} araç", obs + " — iddiadan az", "mismatch"))
        elif claim.get("count_relation") == "more_than_usual" and claim.get("baseline") is not None:
            dens_ids = {vid for d, vid in trk if d <= R_DENSITY_M}
            dens_dets = [d for dist, d in dets if dist <= R_DENSITY_M]
            dens = len(dens_ids | {d["vehicle_id"] for d in dens_dets if d.get("vehicle_id")}) + sum(1 for d in dens_dets if not d.get("vehicle_id"))
            claimed = f"olağandan yoğun (olağan ~{claim['baseline']})"
            obs = f"{dens} araç ({R_DENSITY_M:.0f} m içinde, görüntü+iz)"
            checks.append(_check("yoğunluk", claimed, obs, "match" if dens > claim["baseline"] else "mismatch"))

        # ---- hareket: iddia konumundaki EN YAKIN İZLİ aracın izinden. İzsiz araçta hareket verisi yoktur (doğrulanamaz).
        motion_id: str | None = None
        if claim.get("motion") in ("stationary", "toward_base", "away", "moving"):
            if nearest_trk[0] is not None and nearest_trk[0] <= R_NEAR_M:  # iddia konumundaki araç izliyse; yanlış aracın izi kullanılmaz
                motion_id = nearest_trk[1]
                checks.append(self._motion_check(claim, motion_id))
            elif loc_ok:
                checks.append(_check("hareket", claim["motion"], "çekim anında o konumdaki araç izsiz: hareket verisi YOK", "unverifiable"))

        # ---- doğrulanamayanlar
        unverified: list[str] = []
        if claim.get("identity"):
            checks.append(_check("kimlik", "dost/teyitli", "kendi tespit+iz verimizle doğrulanamaz; risk değerlendirmesinde kullanılmaz", "unverifiable"))
            unverified.append("kimlik")
        if claim.get("color"):
            checks.append(_check("renk", claim["color"], "tespit yalnızca sınıf verir", "unverifiable"))
            unverified.append("renk")
        unverified += [c["aspect"] for c in checks if c["result"] == "unverifiable" and c["aspect"] not in unverified]
        verdict = _verdict(checks)
        extra = ""
        if claim.get("identity") and verdict == "compatible" and claim.get("motion") == "toward_base":
            extra = "(Tarif edilen araç gerçekten üsse yaklaşıyor; 'dost' iddiası bunu değiştirmez.)"
        return {
            "verdict": verdict, "checks": checks, "unverified": unverified, "summary": _summary(verdict, checks, extra),
            "nearest_track_m": None if nearest_trk[0] is None else round(nearest_trk[0], 1), "nearest_track": nearest_trk[1] if nearest_trk[0] is not None else None,
            "motion_track": motion_id, "tracks_near": len(tracked_ids), "in_image_footprint": True, "report_time": iso(t_c),
        }


def verify_claims(store: TrackStore, cfg: Settings, event: dict[str, Any], base: Base, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    v = Verifier(store, cfg, event, base)
    out = []
    for it in items:
        res = v.verify(it)
        res["id"] = it["id"]
        out.append(res)
    return out
