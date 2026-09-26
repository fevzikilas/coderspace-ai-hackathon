"""Kural tabanlı değerlendirme (LLM yokken / bütçe aşılınca / LLM hata verince fallback).

Aynı politika (policy.own_data_level) ve aynı çıktı şemasını kullanır; yalnızca gerekçe ve
evidence_breakdown metni şablonla üretilir.
"""
from __future__ import annotations

from typing import Any

from .facts import Facts
from .policy import LEVELS, OwnLevel, normalize_evidence

_CONVOY_KW = ("kafile", "konvoy", "art arda", "3 araç", "araçlık", "3 arac", "araclik", "konvoy")
_APPROACH_KW = ("yaklaş", "üsse", "doğru ilerliyor", "kapıya", "yaklas", "usse", "ilerliyor")

BASE_WEIGHTS = {"movement": 0.35, "pattern": 0.25, "detection": 0.15, "intel": 0.10, "reports": 0.10, "drone": 0.05}


def _kw_hit(items: list[dict[str, Any]] | None, kws: tuple[str, ...]) -> bool:
    return any(any(k in str(i.get("text", "")).lower() for k in kws) for i in (items or []))


def build_evidence(facts: Facts, own: OwnLevel) -> list[dict[str, Any]]:
    ev: list[dict[str, Any]] = []
    dets = facts.detection.get("detections", [])

    if dets:
        classes = sorted({d.get("class", "?") for d in dets})
        avg = sum(float(d.get("conf", 0)) for d in dets) / len(dets)
        tracked = sum(1 for d in dets if d.get("vehicle_id"))
        where = f"{facts.drone_id} görüntüsünde" if facts.drone_id else "Görüntüde"
        ev.append(
            {
                "source": "detection",
                "weight": BASE_WEIGHTS["detection"],
                "summary": f"{where} {len(dets)} nesne tespit edildi ({', '.join(classes)}); {tracked} tanesi iz kaydıyla eşleşti, ortalama güven {avg:.2f}.",
                "effect": "raises" if len(dets) >= 2 else "neutral",
            }
        )

    if facts.movement:
        parts, approaching = [], False
        for vid in facts.vehicle_ids:
            mv = facts.movement.get(vid)
            if not mv:
                continue
            if mv.get("insufficient_data"):
                parts.append(f"{vid}: veri yetersiz")
                continue
            approaching |= bool(mv.get("approaching"))
            eta = f", ETA {mv['eta_min']:.1f} dk" if mv.get("eta_min") is not None else ""
            state = "üsse yaklaşıyor" if mv.get("approaching") else "yaklaşmıyor"
            parts.append(f"{vid}: {mv['speed_mps']:.1f} m/s, {state}, {mv['distance_to_base_m']:.0f} m{eta}")
        ev.append(
            {
                "source": "movement",
                "weight": BASE_WEIGHTS["movement"],
                "summary": "; ".join(parts) + ".",
                "effect": "raises" if approaching else "neutral",
            }
        )

    if facts.pattern:
        pats = facts.matched_patterns()
        label = " + ".join(pats) if pats else facts.pattern.get("pattern", "RANDOM")
        extra = " CONVOY nedeniyle kademe otomatik yükseltildi." if own.convoy_bumped else ""
        ev.append(
            {
                "source": "pattern",
                "weight": BASE_WEIGHTS["pattern"] if pats else BASE_WEIGHTS["pattern"] / 2,
                "summary": f"Kural tabanlı patern: {label} (güven {facts.pattern.get('confidence', 0):.2f}).{extra}",
                "effect": "raises" if pats else "neutral",
            }
        )

    if facts.drone:
        cap = facts.drone.get("capture", {})
        ev.append(
            {
                "source": "drone",
                "weight": BASE_WEIGHTS["drone"],
                "summary": f"{facts.drone['id']} {facts.drone.get('status')}, irtifa {cap.get('alt')} m, gimbal {cap.get('gimbal_pitch')}°; ölçüm koşulları normal.",
                "effect": "neutral",
            }
        )

    for src, items, label in (("intel", facts.intel, "istihbarat maddesi"), ("reports", facts.reports, "saha raporu")):
        if not items:  # None (çağrılmadı) veya boş (kaynakta madde yok): kanıt olarak ağırlık verme
            continue
        corroborates = own.idx >= 1 and (_kw_hit(items, _CONVOY_KW) or _kw_hit(items, _APPROACH_KW))
        note = "kendi verimizle örtüşen ifadeler içeriyor (yalnızca destekleyici)" if corroborates else "kendi verimizi değiştiren bir bilgi içermiyor"
        split = ""
        if src == "reports":
            n_off = sum(1 for i in items if i.get("source") == "official")
            split = f" (resmî: {n_off}, üçüncü taraf/diğer: {len(items) - n_off})"
        ev.append(
            {
                "source": src,
                "weight": BASE_WEIGHTS[src],
                "summary": f"{len(items)} {label}{split}, DÜŞÜK GÜVEN; {note}.",
                "effect": "raises" if corroborates else "neutral",
            }
        )
    return normalize_evidence(ev)


def build_rationale(facts: Facts, own: OwnLevel, level_idx: int) -> str:
    n = len(facts.vehicle_ids)
    if n == 0:
        return "Tespitte takip edilebilir araç yok; hareket veya patern analizi yapılamadı."
    sents = [f"{n} araç için kendi tespit ve hareket verisi değerlendirildi."]
    approaching = [v for v in facts.vehicle_ids if facts.movement.get(v, {}).get("approaching")]
    if approaching:
        lead = min(approaching, key=lambda v: facts.movement[v].get("distance_to_base_m") or 1e12)
        mv = facts.movement[lead]
        eta = f", tahmini varış ~{mv['eta_min']:.1f} dk" if mv.get("eta_min") is not None else ""
        sents.append(f"{len(approaching)} araç üsse yaklaşıyor (en yakını {lead}: {mv['distance_to_base_m']:.0f} m, {mv['speed_mps']:.1f} m/s{eta}).")
    elif own.insufficient:
        sents.append("Hareket verisi yetersiz; yaklaşma durumu güvenle belirlenemedi.")
    else:
        sents.append("Hiçbir araç üsse yaklaşmıyor.")
    pats = facts.matched_patterns()
    if pats:
        sents.append(f"Patern: {' + '.join(pats)}.")
    if own.convoy_bumped:
        sents.append("CONVOY tespit edildiği için risk kademesi otomatik olarak bir üst seviyeye yükseltildi.")
    if facts.intel or facts.reports:
        sents.append("İstihbarat ve saha raporları düşük güvenli olduğundan yalnızca destekleyici bağlam olarak ele alındı; karar kendi verilerimize dayanıyor.")
    sents.append(f"Sonuç: {LEVELS[level_idx]}.")
    return " ".join(sents)


def build_confidence(facts: Facts, own: OwnLevel) -> float:
    if own.insufficient:
        return 0.3
    conf = 0.55
    if facts.pattern:
        conf += 0.25 * float(facts.pattern.get("confidence", 0)) if facts.matched_patterns() else 0.05
    if any((facts.movement.get(v, {}).get("points_used", 0) or 0) >= 5 for v in facts.vehicle_ids):
        conf += 0.1
    if any(facts.movement.get(v, {}).get("stale") for v in facts.vehicle_ids):
        conf -= 0.15
    return round(max(0.3, min(0.95, conf)), 2)
