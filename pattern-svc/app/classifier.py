"""Kural tabanlı davranış sınıflandırma (LLM YOK).

Desenler ve öncelik (yüksekten düşüğe): CONVOY > DIRECT_APPROACH > LOITERING > RANDOM.
Girdi: araç başına sıralı iz [(ts_epoch, lat, lon), ...] ve üs konumu. Saf fonksiyonlar; I/O yok.
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any

from .config import Settings
from .geo import angle_diff, bearing_deg, haversine_m, to_enu

Track = list[tuple[float, float, float]]

PRIORITY = ["CONVOY", "DIRECT_APPROACH", "LOITERING"]


@dataclass(frozen=True)
class Base:
    lat: float
    lon: float
    radius_m: float = 0.0


# ---------------------------------------------------------------------------------- LOITERING
def detect_loitering(track: Track, p: Settings) -> dict[str, Any] | None:
    """Son `loiter_window_s` içinde tüm noktalar `loiter_radius_m` yarıçapında kalıyorsa.

    Yeterli gözlem süresi şartı: gözlenen süre >= window * min_span_ratio (kısa gözlem 'loitering' kanıtlamaz).
    Yarıçap: ağırlık merkezine uzaklıkların %98'lik dilimi (tek GPS sıçramasına dayanıklı).
    """
    if len(track) < p.loiter_min_points:
        return None
    ref = track[-1][0]
    pts = [x for x in track if ref - x[0] <= p.loiter_window_s]
    span = pts[-1][0] - pts[0][0]
    if len(pts) < p.loiter_min_points or span < p.loiter_window_s * p.loiter_min_span_ratio:
        return None
    lat0, lon0 = pts[0][1], pts[0][2]
    xy = [to_enu(lat0, lon0, x[1], x[2]) for x in pts]
    cx = sum(x for x, _ in xy) / len(xy)
    cy = sum(y for _, y in xy) / len(xy)
    dists = sorted(math.hypot(x - cx, y - cy) for x, y in xy)
    radius = dists[int(0.98 * (len(dists) - 1))]
    if radius >= p.loiter_radius_m:
        return None
    coverage = min(1.0, span / p.loiter_window_s)
    conf = min(0.95, 0.5 + 0.45 * (1 - radius / p.loiter_radius_m) * coverage)
    return {
        "pattern": "LOITERING",
        "confidence": round(conf, 3),
        "metrics": {
            "radius_m": round(radius, 1),
            "observed_min": round(span / 60, 1),
            "n_points": len(pts),
            "limit_radius_m": p.loiter_radius_m,
        },
    }


# ---------------------------------------------------------------------------------- DIRECT_APPROACH
def detect_direct_approach(track: Track, base: Base, p: Settings) -> dict[str, Any] | None:
    """Son `approach_window_s` içinde üsse mesafe monoton azalıyor ve hareket üs istikametinden <15° sapıyor."""
    if not track:
        return None
    ref = track[-1][0]
    pts = [x for x in track if ref - x[0] <= p.approach_window_s]
    if len(pts) < p.approach_min_points:
        return None
    d = [haversine_m(x[1], x[2], base.lat, base.lon) for x in pts]
    closure = d[0] - d[-1]
    if closure < p.approach_min_closure_m:
        return None
    # monotonik azalış (GPS gürültüsü toleransı ile)
    if any(d[i + 1] - d[i] > p.approach_noise_m for i in range(len(d) - 1)):
        return None
    devs: list[float] = []
    for a, b in zip(pts, pts[1:]):
        if haversine_m(a[1], a[2], b[1], b[2]) < p.approach_min_segment_m:  # gürültü seviyesindeki adımlar yön vermez
            continue
        move = bearing_deg(a[1], a[2], b[1], b[2])
        to_base = bearing_deg(a[1], a[2], base.lat, base.lon)
        devs.append(abs(angle_diff(move, to_base)))
    if not devs:
        return None
    mean_dev = sum(devs) / len(devs)
    if mean_dev >= p.approach_max_dev_deg:
        return None
    conf = min(0.98, 0.6 + 0.3 * (1 - mean_dev / p.approach_max_dev_deg) + 0.1 * min(1.0, closure / 1000.0))
    return {
        "pattern": "DIRECT_APPROACH",
        "confidence": round(conf, 3),
        "metrics": {
            "closure_m": round(closure, 1),
            "distance_start_m": round(d[0], 1),
            "distance_now_m": round(d[-1], 1),
            "mean_heading_deviation_deg": round(mean_dev, 1),
            "window_s": round(pts[-1][0] - pts[0][0], 1),
            "n_points": len(pts),
        },
    }


# ---------------------------------------------------------------------------------- CONVOY
@dataclass
class _State:
    lat: float
    lon: float
    speed: float
    heading: float


def _interp(track: Track, tss: list[float], t: float) -> tuple[float, float] | None:
    """t anındaki doğrusal enterpolasyonlu konum; iz kapsamı dışında None (ekstrapolasyon yok)."""
    if t < tss[0] or t > tss[-1]:
        return None
    i = bisect.bisect_left(tss, t)
    if tss[i] == t or i == 0:
        return track[i][1], track[i][2]
    a, b = track[i - 1], track[i]
    f = (t - a[0]) / (b[0] - a[0])
    return a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2])


def _state_at(track: Track, tss: list[float], t: float, lookback: float) -> _State | None:
    now = _interp(track, tss, t)
    before = _interp(track, tss, t - lookback)
    if now is None or before is None:
        return None
    dist = haversine_m(before[0], before[1], now[0], now[1])
    speed = dist / lookback
    heading = bearing_deg(before[0], before[1], now[0], now[1]) if dist > 0.5 else 0.0
    return _State(now[0], now[1], speed, heading)


def _pair_ok(a: _State, b: _State, p: Settings) -> bool:
    if haversine_m(a.lat, a.lon, b.lat, b.lon) >= p.convoy_dist_m:
        return False
    if a.speed < p.convoy_min_speed_mps or b.speed < p.convoy_min_speed_mps:
        return False
    if abs(angle_diff(a.heading, b.heading)) >= p.convoy_heading_tol_deg:
        return False
    return abs(a.speed - b.speed) / max(a.speed, b.speed) <= p.convoy_speed_ratio


def detect_convoy(tracks: dict[str, Track], p: Settings) -> dict[str, Any] | None:
    """2+ araç: son pencerede >=%80 örnekte aynı anda <500 m, aynı yön (±20°), benzer hız."""
    usable = {vid: t for vid, t in tracks.items() if len(t) >= 3}
    if len(usable) < 2:
        return None
    t_end = max(t[-1][0] for t in usable.values())
    active = {vid: t for vid, t in usable.items() if t_end - t[-1][0] <= p.convoy_max_gap_s}
    if len(active) < 2:
        return None

    n_steps = int(p.convoy_window_s / p.convoy_sample_s)
    sample_ts = [t_end - p.convoy_window_s + i * p.convoy_sample_s for i in range(n_steps + 1)]
    tss = {vid: [x[0] for x in t] for vid, t in active.items()}
    lookback = max(20.0, p.convoy_sample_s)  # hız, bir örnekleme adımı geriye bakarak hesaplanır
    states = {vid: [_state_at(active[vid], tss[vid], t, lookback) for t in sample_ts] for vid in active}

    parent = {vid: vid for vid in active}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges: list[tuple[str, str, float, float]] = []
    for a, b in combinations(sorted(active), 2):
        valid = ok = 0
        gaps: list[float] = []
        for sa, sb in zip(states[a], states[b]):
            if sa is None or sb is None:
                continue
            valid += 1
            if _pair_ok(sa, sb, p):
                ok += 1
                gaps.append(haversine_m(sa.lat, sa.lon, sb.lat, sb.lon))
        if valid >= p.convoy_min_samples and ok / valid >= p.convoy_min_frac:
            edges.append((a, b, ok / valid, sum(gaps) / len(gaps)))
            parent[find(a)] = find(b)
    if not edges:
        return None

    groups: dict[str, set[str]] = {}
    for a, b, _f, _g in edges:
        groups.setdefault(find(a), set()).update((a, b))
    members = sorted(max(groups.values(), key=len))
    used = [e for e in edges if e[0] in members and e[1] in members]
    mean_frac = sum(e[2] for e in used) / len(used)
    mean_gap = sum(e[3] for e in used) / len(used)
    speeds = [s.speed for vid in members for s in states[vid] if s is not None]
    conf = min(0.98, 0.6 + 0.3 * mean_frac + 0.05 * (len(members) - 2))
    return {
        "pattern": "CONVOY",
        "confidence": round(conf, 3),
        "vehicles": members,
        "metrics": {
            "n_vehicles": len(members),
            "sync_fraction": round(mean_frac, 3),
            "mean_pair_gap_m": round(mean_gap, 1),
            "mean_speed_mps": round(sum(speeds) / len(speeds), 2) if speeds else 0.0,
            "window_s": p.convoy_window_s,
        },
    }


# ---------------------------------------------------------------------------------- örnekleme uyarlaması
def sampling_step(tracks: dict[str, Track]) -> float:
    """Tüm izlerdeki ardışık noktalar arası MEDYAN süre (sn); yeterli veri yoksa 0."""
    diffs = [b[0] - a[0] for t in tracks.values() for a, b in zip(t, t[1:]) if b[0] > a[0]]
    return float(statistics.median(diffs)) if diffs else 0.0


def effective_params(p: Settings, step_s: float) -> Settings:
    """Eşikleri veri örnekleme aralığına uyarlar. Varsayılanlar 15 sn'lik yoğun izler içindir; 5 dk'lık seyrek izde
    300 sn'lik pencere 1-2 nokta içerir ve DIRECT_APPROACH/CONVOY hiç tetiklenemezdi. Yoğun izde (adım ≤ convoy_sample_s)
    hiçbir şey değişmez.
    """
    if step_s <= p.convoy_sample_s:
        return p
    return replace(
        p,
        approach_window_s=max(p.approach_window_s, step_s * (p.approach_min_points - 1) * 1.5),
        approach_min_segment_m=max(p.approach_min_segment_m, p.approach_noise_m),
        convoy_sample_s=step_s,
        convoy_window_s=max(p.convoy_window_s, step_s * (p.convoy_min_samples + 1)),
        convoy_min_samples=min(p.convoy_min_samples, 4),
        convoy_max_gap_s=max(p.convoy_max_gap_s, 2 * step_s),
    )


# ---------------------------------------------------------------------------------- birleştirme
def classify(tracks: dict[str, Track], base: Base, p: Settings) -> dict[str, Any]:
    """Tüm araçlar için desen sınıflandırması.

    Dönüş: {"pattern", "confidence", "involved_vehicles", "detail"}; detail.per_vehicle her aracın kendi
    etiketini taşır, detail.matched_patterns eşleşen TÜM desenleri (ör. CONVOY + DIRECT_APPROACH) listeler.
    Zaman referansı her izin SON noktasıdır (çağıran izleri `reference_time`'a kadar keser); duvar saati kullanılmaz.
    """
    step_s = sampling_step(tracks)
    p = effective_params(p, step_s)
    per_vehicle: dict[str, dict[str, Any]] = {vid: {"matched": [], "metrics": {}} for vid in tracks}
    agg: dict[str, dict[str, Any]] = {}

    def add(pattern: str, vid: str, res: dict[str, Any]) -> None:
        per_vehicle[vid]["matched"].append(pattern)
        per_vehicle[vid]["metrics"][pattern] = res["metrics"]
        entry = agg.setdefault(pattern, {"pattern": pattern, "vehicles": [], "confidences": [], "metrics": {}})
        entry["vehicles"].append(vid)
        entry["confidences"].append(res["confidence"])
        entry["metrics"][vid] = res["metrics"]

    for vid, track in tracks.items():
        if (r := detect_loitering(track, p)) is not None:
            add("LOITERING", vid, r)
        if (r := detect_direct_approach(track, base, p)) is not None:
            add("DIRECT_APPROACH", vid, r)

    convoy = detect_convoy(tracks, p)
    if convoy is not None:
        for vid in convoy["vehicles"]:
            per_vehicle[vid]["matched"].append("CONVOY")
            per_vehicle[vid]["metrics"]["CONVOY"] = convoy["metrics"]
        agg["CONVOY"] = {
            "pattern": "CONVOY",
            "vehicles": convoy["vehicles"],
            "confidences": [convoy["confidence"]],
            "metrics": convoy["metrics"],
        }

    matched_patterns = []
    for name in PRIORITY:
        e = agg.get(name)
        if e is None:
            continue
        matched_patterns.append(
            {
                "pattern": name,
                "confidence": round(max(e["confidences"]) if name == "CONVOY" else sum(e["confidences"]) / len(e["confidences"]), 3),
                "vehicles": sorted(e["vehicles"]),
                "metrics": e["metrics"],
            }
        )

    for vid, info in per_vehicle.items():
        info["pattern"] = next((n for n in PRIORITY if n in info["matched"]), "RANDOM")
        info["n_points"] = len(tracks[vid])

    insufficient = sorted(vid for vid, t in tracks.items() if len(t) < 3)
    if matched_patterns:
        top = matched_patterns[0]
        pattern, confidence, involved = top["pattern"], top["confidence"], top["vehicles"]
    else:
        total = sum(len(t) for t in tracks.values())
        quality = min(1.0, total / max(1, 20 * max(1, len(tracks))))
        pattern, confidence, involved = "RANDOM", round(0.35 + 0.35 * quality, 3), sorted(tracks)

    return {
        "pattern": pattern,
        "confidence": confidence,
        "involved_vehicles": involved,
        "detail": {
            "matched_patterns": matched_patterns,
            "per_vehicle": per_vehicle,
            "insufficient_data": insufficient,
            "sampling_step_s": round(step_s, 1),
            "priority": PRIORITY + ["RANDOM"],
            "base": {"lat": base.lat, "lon": base.lon, "radius_m": base.radius_m},
            "rules": {
                "LOITERING": f"{p.loiter_window_s / 3600:g} saatte < {p.loiter_radius_m:g} m yarıçap",
                "DIRECT_APPROACH": f"son {p.approach_window_s:g} sn'de mesafe monoton azalıyor, sapma < {p.approach_max_dev_deg:g}°",
                "CONVOY": f"2+ araç < {p.convoy_dist_m:g} m, aynı yön (±{p.convoy_heading_tol_deg:g}°), senkron hız",
            },
        },
    }
