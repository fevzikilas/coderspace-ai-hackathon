"""Saha raporu metninden YAPILANDIRILMIŞ iddia çıkarır (deterministik, sözlük tabanlı; LLM yok).

Amaç: raporun ne iddia ettiğini (araç tipi, sayı, hareket, kimlik, bölge düzeyi durum) makinece karşılaştırılabilir hale getirmek.
Karşılaştırma (uyumlu/uyumsuz) core-svc'de kendi tespit+iz verisiyle yapılır; burada YALNIZCA metin çözümlenir.

Şema (`claim`):
  kind        : "vehicle"       konumlu araç iddiası (koordinat + tip/sayı/hareket)
                "zone_no_heavy" bölge düzeyi: ağır araç yok, yalnızca binek araç
                "zone_activity" bölge düzeyi: olağan/sakin durum iddiası
                "context"       doğrulanabilir araç iddiası içermeyen bağlam (hava, telsiz, plan, geçmiş ihbar …)
  types       : ["truck"|"car"|"van"|"bus"]  (model sınıfları; boşsa "araç" genel)
  count       : bildirilen araç sayısı (yoksa None) · convoy: "3 araçlık konvoy"
  baseline    : "olağan trafik N araç" gibi bir referans sayı; count_relation="more_than_usual" ile birlikte gelir
  motion      : "stationary" | "toward_base" | "away" | "moving" | "normal" | None
  min_duration_min : "bir saatten uzun" → 60, "uzun süredir" → 30
  identity    : "friendly"  ("dost", "bize bağlı", "kimlik teyidi yapılmıştır", "planlı ikmal aracıdır") — kendi verimizle DOĞRULANAMAZ
  color       : rapor edilen renk (tespit yalnızca sınıf verir; doğrulanamaz)
  hearsay     : "ihbar / doğrulanmamış / iletti / bildirildi" (kaynak zayıflığı işareti)
  self_unverified : rapor kendisi "doğrulanamadı" diyor
  context_reason  : kind == "context" ise nedeni
"""
from __future__ import annotations

import re
from typing import Any

from .loaders.zones import fold

_TYPES: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\bagir (?:bir )?arac"), ["truck", "bus"]),
    (re.compile(r"\bkamyon"), ["truck"]),
    (re.compile(r"\botobus"), ["bus"]),
    (re.compile(r"\bpanelvan|\bminibus|\bvan\b"), ["van"]),
    (re.compile(r"\botomobil|\bbinek arac"), ["car"]),
]
_NUM_WORDS = {"bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5}
_COUNT_RE = re.compile(r"\b(\d+|bir|iki|uc|dort|bes)\s+(?:adet\s+)?(?:(?:mavi|kirmizi|sari|beyaz|siyah|yesil|gri)\s+)?(kamyon|otomobil|panelvan|otobus|minibus|agir (?:bir )?arac|arac)")
_CONVOY_RE = re.compile(r"\b(\d+)\s+araclik")
_COLOR_LEAD_RE = re.compile(r"\b(mavi|kirmizi|sari|beyaz|siyah|yesil|gri)\b(?:\s+bir)?\s+(kamyon|otomobil|panelvan|otobus|arac|agir arac)")
_COLOR_BARE_RE = re.compile(r"\b(mavi|kirmizi|sari|beyaz|siyah|yesil|gri)\b")
_BASELINE_RE = re.compile(r"(?:genellikle|olagan trafik)\s+(\d+)\s+arac")
_STATIONARY = re.compile(r"hareketsiz|durdugu|\bdurdu\b|park halinde|beklemede|\bbekliyor|yerinden ayrilmadi|durmakta")
_TOWARD = re.compile(r"usse (?:dogru ilerleyen|gelen)")
_AWAY = re.compile(r"uzaklasiyor|uzaklasan")
_MOVING = re.compile(r"\bilerliyor|transit geciyor")
_NORMAL_ACT = re.compile(r"trafik akisi normal|olagandisi (?:bir )?durum bildirmedi|kayda deger bir hareketlilik bulunmuyor|hareketleri olagan")
_IDENTITY = re.compile(r"\bdost\b|bize bagli|kimlik teyid|planli ikmal aracidir|teyitli")
_HEARSAY = re.compile(r"ihbar|dogrulanmamis|iletti|bildirildi|bir kaynak")
_SELF_UNVERIFIED = re.compile(r"dogrulanamadi")

# (desen, neden) — doğrulanabilir araç iddiası taşımayan bağlam cümleleri
_CONTEXT: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"hava acik|gorus mesafesi"), "hava durumu"),
    (re.compile(r"telsiz baglantisi"), "haberleşme durumu"),
    (re.compile(r"\bdun gece\b"), "geçmiş (dün gece) doğrulanmamış ihbar"),
    (re.compile(r"ihbar incelendi"), "rapor kendisi doğrulanamadığını söylüyor"),
    (re.compile(r"yola cikacak|planlanan saatte"), "gelecek plan (lojistik konvoy)"),
    (re.compile(r"planli tatbikat"), "genel bağlam (planlı tatbikat / dost unsur)"),
]

_COORD_RE = re.compile(r"-?\d{1,2}\.\d+\s*°?\s*N\b[\s,;]*-?\d{1,3}\.\d+\s*°?\s*E\b", re.IGNORECASE)


def _types_in(t: str) -> list[str]:
    out: list[str] = []
    for pat, ts in _TYPES:
        if pat.search(t):
            for x in ts:
                if x not in out:
                    out.append(x)
    return out


def _count(t: str) -> tuple[int | None, bool]:
    m = _CONVOY_RE.search(t)
    if m:
        return int(m.group(1)), True
    m = _COUNT_RE.search(t)
    if m:
        w = m.group(1)
        return (int(w) if w.isdigit() else _NUM_WORDS[w]), False
    return None, False


def parse_claim(text: str) -> dict[str, Any]:
    t = fold(text)
    has_coord = bool(_COORD_RE.search(text))
    claim: dict[str, Any] = {
        "kind": "context", "types": _types_in(t), "count": None, "convoy": False, "baseline": None, "count_relation": None,
        "motion": None, "min_duration_min": None, "identity": "friendly" if _IDENTITY.search(t) else None,
        "color": None, "hearsay": bool(_HEARSAY.search(t)), "self_unverified": bool(_SELF_UNVERIFIED.search(t)), "context_reason": None,
    }
    cm = _COLOR_LEAD_RE.search(t) or _COLOR_BARE_RE.search(t)
    if cm:
        claim["color"] = cm.group(1)

    for pat, why in _CONTEXT:  # açık bağlam cümleleri (koordinat taşımazlar)
        if pat.search(t) and not has_coord:
            claim["context_reason"] = why
            return claim

    if "agir arac hareketi yok" in t:
        claim.update(kind="zone_no_heavy", types=["truck", "bus"])
        return claim

    claim["count"], claim["convoy"] = _count(t)
    bm = _BASELINE_RE.search(t)
    if bm:
        claim["baseline"] = int(bm.group(1))
        if "olagandan yogun" in t or "beklenmedik bir yogunluk" in t:
            claim["count_relation"] = "more_than_usual"
        claim["count"] = None  # "4 araç civarı" referanstır, bildirilen sayı değil
    elif claim["count"] is not None:
        claim["count_relation"] = "exact"

    if _STATIONARY.search(t):
        claim["motion"] = "stationary"
        if "bir saatten uzun" in t:
            claim["min_duration_min"] = 60
        elif "uzun suredir" in t:
            claim["min_duration_min"] = 30
    elif _TOWARD.search(t):
        claim["motion"] = "toward_base"
    elif _AWAY.search(t):
        claim["motion"] = "away"
    elif _MOVING.search(t):
        claim["motion"] = "moving"
    elif _NORMAL_ACT.search(t):
        claim["motion"] = "normal"

    if has_coord and (claim["types"] or claim["count"] is not None or claim["motion"] or claim["baseline"] or "arac" in t):
        claim["kind"] = "vehicle"
    elif not has_coord and claim["motion"] == "normal":
        claim["kind"] = "zone_activity"
    else:
        claim["context_reason"] = "doğrulanabilir araç iddiası yok"
    return claim
