"""Deterministik risk politikası.

Amaç: (1) kendi tespit+hareket verisi > dış istihbarat/rapor önceliğini LLM'den bağımsız GARANTİ etmek,
(2) CONVOY paterninde kademeyi otomatik yükseltmek, (3) evidence_breakdown'ı kanonik güven düzeyleriyle
normalize etmek. LLM bu politikanın çıktısını 'kendi verisi tabanı' olarak alır ve en fazla bir kademe
yukarı oynatabilir; aşağı çekemez.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .facts import Facts

LEVELS = ("LOW", "MEDIUM", "HIGH")
SOURCES = ("detection", "movement", "pattern", "drone", "intel", "reports")
# Kaynak sınıfına göre KANONİK güven (LLM'in verdiği trust yok sayılır)
TRUST = {
    "detection": "high",
    "movement": "high",
    "pattern": "high",
    "drone": "medium",
    "intel": "low",
    "reports": "low",
}
EFFECTS = ("raises", "neutral", "lowers")
LOW_TRUST_CAP = 0.20  # düşük güvenli kaynakların toplam ağırlık tavanı

# Eşikler
NEAR_M = 5000.0  # "yakın" sayılan mesafe
ETA_HIGH_MIN = 10.0
ETA_MEDIUM_MIN = 30.0


def level_index(level: str) -> int:
    return LEVELS.index(str(level).strip().upper())


@dataclass
class OwnLevel:
    idx: int
    base_idx: int  # CONVOY yükseltmesinden önceki kademe
    reasons: list[str] = field(default_factory=list)
    convoy_bumped: bool = False
    insufficient: bool = False


def movement_level(mv: dict[str, Any], base_radius_m: float) -> tuple[int, str]:
    """Tek araç hareket analizinden kademe (0..2) ve gerekçe."""
    if mv.get("insufficient_data") or mv.get("distance_to_base_m") is None:
        return 0, "hareket verisi yetersiz"
    dist = float(mv["distance_to_base_m"])
    eta = mv.get("eta_min")
    if mv.get("within_base"):
        return 2, f"üs sınırı içinde ({dist:.0f} m)"
    if mv.get("approaching"):
        if eta is not None and eta <= ETA_HIGH_MIN:
            return 2, f"üsse yaklaşıyor ({dist:.0f} m, ETA {eta:.1f} dk ≤ {ETA_HIGH_MIN:g} dk)"
        if dist <= NEAR_M or (eta is not None and eta <= ETA_MEDIUM_MIN):
            eta_txt = f", ETA {eta:.1f} dk" if eta is not None else ""
            return 1, f"üsse yaklaşıyor ({dist:.0f} m{eta_txt})"
        return 0, f"yaklaşma var ama uzak ({dist:.0f} m)"
    if dist <= 2 * base_radius_m:
        return 1, f"üs çevresinde ({dist:.0f} m), yaklaşmıyor"
    return 0, f"yaklaşmıyor ({dist:.0f} m)"


def own_data_level(facts: Facts) -> OwnLevel:
    """Yalnızca KENDİ verilerinden (hareket + patern) belirlenen taban kademe."""
    reasons: list[str] = []
    levels: list[int] = []
    any_approaching = False
    dists: list[float] = []
    for vid in facts.vehicle_ids:
        mv = facts.movement.get(vid)
        if mv is None:
            continue
        lvl, why = movement_level(mv, float(facts.base.get("radius_m", 1500.0)))
        levels.append(lvl)
        reasons.append(f"Hareket {vid}: {why} → {LEVELS[lvl]}")
        any_approaching |= bool(mv.get("approaching"))
        if mv.get("distance_to_base_m") is not None:
            dists.append(float(mv["distance_to_base_m"]))
    insufficient = not levels or all(facts.movement.get(v, {}).get("insufficient_data") for v in facts.vehicle_ids)
    idx = max(levels, default=0)

    pats = facts.matched_patterns()
    if "DIRECT_APPROACH" in pats and idx < 1:
        idx = 1
        reasons.append("Patern: DIRECT_APPROACH → en az MEDIUM")
    if "LOITERING" in pats and dists and min(dists) <= NEAR_M and idx < 1:
        idx = 1
        reasons.append(f"Patern: LOITERING üs yakınında ({min(dists):.0f} m) → en az MEDIUM")

    base_idx = idx
    bumped = False
    if "CONVOY" in pats:
        new_idx = 2 if (any_approaching or "DIRECT_APPROACH" in pats) else min(2, idx + 1)
        if new_idx > idx:
            reasons.append(f"Patern: CONVOY tespit edildi → risk kademesi otomatik yükseltildi ({LEVELS[idx]}→{LEVELS[new_idx]})")
            idx, bumped = new_idx, True
        else:
            reasons.append("Patern: CONVOY tespit edildi (kademe zaten en üstte)")
    return OwnLevel(idx=idx, base_idx=base_idx, reasons=reasons, convoy_bumped=bumped, insufficient=insufficient)


def enforce(llm_idx: int, own: OwnLevel) -> tuple[int, list[str]]:
    """LLM kademesini politika bandına oturtur: [own, min(own+1, HIGH)].

    - Aşağı çekemez: kendi tespit+hareket verisi tabanı korur.
    - Kendi verisi LOW iken istihbarat/rapor tek başına en fazla MEDIUM'a çıkarabilir.
    """
    lo, hi = own.idx, min(2, own.idx + 1)
    final = max(lo, min(llm_idx, hi))
    notes: list[str] = []
    if final > llm_idx:
        notes.append(f"LLM kademesi {LEVELS[llm_idx]} politika tabanının ({LEVELS[lo]}) altındaydı; {LEVELS[final]}'e yükseltildi.")
    elif final < llm_idx:
        cap_reason = "kendi verisi LOW iken istihbarat/rapor tek başına MEDIUM'u aşamaz" if own.idx == 0 else "kendi verisinin bir kademe üstünü aşamaz"
        notes.append(f"LLM kademesi {LEVELS[llm_idx]} sınırlandı → {LEVELS[final]} ({cap_reason}).")
    return final, notes


def normalize_evidence(raw: Any) -> list[dict[str, Any]]:
    """LLM'in evidence_breakdown'ını temizler: bilinmeyen kaynakları atar, ağırlıkları toplam 1'e normalize eder,
    trust'ı kanonik değerle değiştirir, düşük güvenli kaynakların toplamını LOW_TRUST_CAP ile sınırlar."""
    if not isinstance(raw, list):
        return []
    merged: dict[str, dict[str, Any]] = {}
    for it in raw:
        if not isinstance(it, dict):
            continue
        src = str(it.get("source", "")).strip().lower()
        if src not in SOURCES:
            continue
        try:
            w = max(0.0, float(it.get("weight", 0) or 0))
        except (TypeError, ValueError):
            w = 0.0
        effect = str(it.get("effect", "neutral")).strip().lower()
        e = merged.setdefault(src, {"source": src, "weight": 0.0, "summary": "", "effect": "neutral"})
        e["weight"] += w
        summary = str(it.get("summary", "")).strip()[:500]
        if summary and not e["summary"]:
            e["summary"] = summary
        if effect in EFFECTS and e["effect"] == "neutral":
            e["effect"] = effect
    items = [e for e in merged.values() if e["summary"]]
    if not items:
        return []

    total = sum(e["weight"] for e in items)
    if total <= 0:
        for e in items:
            e["weight"] = 1.0 / len(items)
    else:
        for e in items:
            e["weight"] /= total

    low = [e for e in items if TRUST[e["source"]] == "low"]
    rest = [e for e in items if TRUST[e["source"]] != "low"]
    low_sum = sum(e["weight"] for e in low)
    if rest and low_sum > LOW_TRUST_CAP:
        for e in low:
            e["weight"] *= LOW_TRUST_CAP / low_sum
        rest_sum = sum(e["weight"] for e in rest)
        for e in rest:
            e["weight"] *= (1.0 - LOW_TRUST_CAP) / rest_sum

    for e in items:
        e["weight"] = round(e["weight"], 3)
        e["trust"] = TRUST[e["source"]]
    drift = round(1.0 - sum(e["weight"] for e in items), 3)
    if drift:  # yuvarlama artığını en büyük paya ekle
        top = max(items, key=lambda e: e["weight"])
        top["weight"] = round(top["weight"] + drift, 3)
    items.sort(key=lambda e: -e["weight"])
    return [{"source": e["source"], "weight": e["weight"], "summary": e["summary"], "trust": e["trust"], "effect": e["effect"]} for e in items]
