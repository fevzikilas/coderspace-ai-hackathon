#!/usr/bin/env python3
"""Simülasyon (tanıtım) sayfası için statik veri: DB'deki ÖNCEDEN HESAPLANMIŞ kural tabanlı sonuçları tek JSON'a döker.

  python scripts/export_simulation_data.py [--out web-ui/public/sim-data] [--images data/REAL/images]
                                           [--psql "docker compose exec -T db sh -c 'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -At'"]

Hiçbir servise / LLM'e istek atmaz; yalnızca Postgres'i OKUR (varsayılan: çalışan compose `db` konteyneri üzerinden psql).
Her görüntü için mode='rule-based' olan EN SON değerlendirme seçilir; o değerlendirmenin tespit olayındaki D-FINE kutuları,
tool_calls_log'daki hareket/patern sonuçları ve kanıt özetlerinden sade, tek cümlelik Türkçe açıklama üretilir.
Ortadaki kutudaki gerekçe: aynı görüntü için DB'de AYNI risk seviyesinde bir LLM değerlendirmesi varsa onun metni, yoksa kural motorunun
gerekçesi (kaynak `explanation_source`); iç terimler (DIRECT_APPROACH, HIGH, car…) yalnızca görüntüleme için Türkçeleştirilir.
Harita: üs + bölgeler `<data>/zones.json`, araç izleri `<data>/tracks.csv` (olay anına kadar son 10 dk) — dosyadan okunur.
Çıktı: <out>/events.json + <out>/images/<image_id>.jpg (Pillow varsa en fazla 1280 px genişliğe küçültülür, yoksa kopyalanır).
Yalnızca standart kütüphane (+ isteğe bağlı Pillow).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PSQL = "docker compose exec -T db sh -c 'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -At'"

# Her görüntünün en son kural tabanlı değerlendirmesi + o olayın tespit kutuları (tek satır = tek JSON)
SQL = """
select json_build_object(
  'image_id', d.image_id, 'detection_id', d.detection_id, 'zone_id', d.zone_id, 'ts', d.ts, 'image_meta', d.image_meta,
  'risk_level', a.risk_level, 'confidence', a.confidence, 'rationale', a.rationale, 'computed_at', a.created_at,
  'evidence', a.evidence_breakdown, 'tools', a.tool_calls_log,
  'objects', (select coalesce(json_agg(json_build_object('cls', o.class, 'conf', o.conf, 'bbox', o.bbox, 'vehicle_id', o.vehicle_id, 'lat', o.lat, 'lon', o.lon)
                                       order by o.box_index), '[]'::json)
              from detection_objects o where o.detection_id = d.detection_id),
  'llm', (select json_build_object('risk_level', x.risk_level, 'rationale', x.rationale, 'model', x.model)
          from assessments x join detections dx using (detection_id)
          where dx.image_id = d.image_id and x.mode = 'llm' order by x.created_at desc limit 1))
from (select distinct on (d.image_id) a.*, d.image_id
      from assessments a join detections d using (detection_id)
      where a.mode = 'rule-based'
      order by d.image_id, a.created_at desc) a
join detections d on d.detection_id = a.detection_id
order by d.ts, d.image_id;
"""

# Görüntüleme adları (web-ui/src/zoneLabels.ts ile aynı; veri ASCII'ye katlanmış ad taşır)
ZONE_LABELS = {
    "kuzey-yolu": "Kuzey Yolu", "kuzeydogu-kavsagi": "Kuzeydoğu Kavşağı", "dogu-yolu": "Doğu Yolu",
    "guneydogu-yerlesimi": "Güneydoğu Yerleşimi", "guney-kapisi-yaklasimi": "Güney Kapısı Yaklaşımı",
    "guneybati-yolu": "Güneybatı Yolu", "bati-yerlesimi": "Batı Yerleşimi", "kuzeybati-yolu": "Kuzeybatı Yolu",
}
CLASS_TR = {"car": "otomobil", "van": "kamyonet", "truck": "kamyon", "bus": "otobüs"}
TRAIL_MIN = 10

# Gerekçe metnindeki iç terimlerin görüntüleme karşılıkları (veri değişmez)
TERMS = [
    (r"\(?approaching\s*[=:]\s*(true|false)\)?", ""), (r"heading deviation", "yön sapması"),
    (r"\bDIRECT_APPROACH\b", "doğrudan yaklaşma"), (r"\bCONVOY\b", "kafile"), (r"\bLOITERING\b", "bekleme"), (r"\bRANDOM\b", "rastgele"),
    (r"\bHIGH\b", "YÜKSEK"), (r"\bMEDIUM\b", "ORTA"), (r"\bLOW\b", "DÜŞÜK"),
    (r"\bincompatible\b", "uyumsuz"), (r"\bcompatible\b", "uyumlu"), (r"\birrelevant\b", "ilgisiz"), (r"\bunverifiable\b", "doğrulanamaz"),
    (r"\bPattern\b", "Patern"), (r"\bETAs?\b", "tahmini varış"),
    (r"\bcars?\b", "otomobil"), (r"\bvans?\b", "kamyonet"), (r"\btrucks?\b", "kamyon"), (r"\bbus(es)?\b", "otobüs"),
]


def display_text(text: str) -> str:
    # politikanın sona eklediği tekrar notları (rationale.ts ile aynı: kutuda gösterilmez)
    text = re.sub(r"\s*\((Veri boşluğu|Politika düzeltmesi)[^()]*(\([^()]*\)[^()]*)*\)\s*$", "", text)
    for pat, rep_ in TERMS:
        text = re.sub(pat, rep_, text)
    text = re.sub(r"(^|[.!?]\s+)([a-zçğıöşü])", lambda m: m.group(1) + ("I" if m.group(2) == "ı" else "İ" if m.group(2) == "i" else m.group(2).upper()), text)
    return re.sub(r"\s+([,.;)])", r"\1", re.sub(r"\(\s*\)", "", re.sub(r"[ \t]{2,}", " ", text))).strip()


def load_map_data(data_dir: Path, base_radius_m: float) -> tuple[dict, list[dict], dict[str, list[tuple[int, float, float]]]]:
    z = json.loads((data_dir / "zones.json").read_text(encoding="utf-8"))
    base = {"name": "Üs", "lat": z["base"]["lat"], "lon": z["base"]["lon"], "radius_m": base_radius_m}
    zones = []
    for item in z["zones"]:
        zid = re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-")
        zones.append({"zone_id": zid, "name": ZONE_LABELS.get(zid, item["name"]), "lat": item["center"][0], "lon": item["center"][1]})
    tracks: dict[str, list[tuple[int, float, float]]] = {}
    with open(data_dir / "tracks.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            h, m = row["time"].split(":")
            tracks.setdefault(row["track_id"], []).append((int(h) * 60 + int(m), float(row["lat"]), float(row["lon"])))
    for pts in tracks.values():
        pts.sort()
    return base, zones, tracks


def fetch_rows(psql: str) -> list[dict]:
    proc = subprocess.run(shlex.split(psql), input=SQL, capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        sys.exit(f"psql başarısız ({proc.returncode}): {proc.stderr.strip()}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip().startswith("{")]


def eta_text(eta_min: float) -> str:
    if eta_min < 1:
        return "1 dakikadan kısa sürede"
    return f"yaklaşık {round(eta_min)} dakika içinde"


def plain_summary(movements: list[dict], matched: list[str], n_boxes: int) -> str:
    """Sade, tek cümle (web-ui/src/summary.ts ile aynı mantık; teknik terim yok)."""
    inside = [m for m in movements if m.get("within_base")]
    approaching = sorted((m for m in movements if m.get("approaching") and not m.get("within_base")),
                         key=lambda m: (m.get("eta_min") if m.get("eta_min") is not None else 1e9, m.get("distance_to_base_m") or 1e12))
    if inside:
        return f"{len(inside)} araç üs sınırının içinde görülüyor." if len(inside) > 1 else "Bir araç üs sınırının içinde görülüyor."
    if approaching:
        lead = approaching[0]
        how = "birlikte (kafile halinde) " if "CONVOY" in matched and len(approaching) > 1 else ""
        if lead.get("eta_min") is not None:
            when = f"{eta_text(lead['eta_min'])} üsse varabilir"
        else:
            when = f"üsse {lead['distance_to_base_m'] / 1000:.1f} km uzakta"
        if len(approaching) == 1:
            return f"Bir araç {how}üsse yaklaşıyor, {when}."
        return f"{len(approaching)} araç {how}üsse yaklaşıyor, en yakını {when}."
    if movements:
        wait = ", bazıları bölgede bekliyor" if "LOITERING" in matched else ""
        return ("İzlenen araç üsse yaklaşmıyor" if len(movements) == 1
                else f"İzlenen {len(movements)} aracın hiçbiri üsse yaklaşmıyor") + f"{wait}."
    if n_boxes:
        return f"Görüntüde {n_boxes} araç var ama nereye gittikleri bilinmiyor; dikkatle izleniyor."
    return "Görüntüde araç görülmedi."


def plain_notes(untracked: int, incompatible: int) -> list[str]:
    notes = []
    if untracked:
        notes.append(f"{untracked} aracın nereye gittiği bilinmiyor.")
    if incompatible:
        notes.append(f"{incompatible} saha raporu gördüklerimizle çelişiyor, dikkate alınmadı.")
    return notes


def build_event(r: dict, tracks: dict[str, list[tuple[int, float, float]]]) -> dict:
    tools = r.get("tools") or []
    movements = [t["result"] for t in tools if t.get("tool") == "get_movement_analysis" and t.get("status") == "ok" and isinstance(t.get("result"), dict)]
    pattern = next((t["result"] for t in tools if t.get("tool") == "get_pattern_classification" and isinstance(t.get("result"), dict)), {}) or {}
    matched = [p["pattern"] for p in pattern.get("matched_patterns") or [] if p.get("pattern")]
    reports = next((t["result"] for t in tools if t.get("tool") == "get_reports" and isinstance(t.get("result"), dict)), {}) or {}
    incompatible = ((reports.get("verification") or {}).get("by_verdict") or {}).get("incompatible") or 0
    if not incompatible:  # eski kayıtlarda sayı yalnızca metinde
        s = next((t.get("result_summary") or "" for t in tools if t.get("tool") == "get_reports"), "")
        tok = s.split(" uyumsuz")[0].split()[-1:] if " uyumsuz" in s else []
        incompatible = int(tok[0]) if tok and tok[0].isdigit() else 0
    objects = r.get("objects") or []
    untracked = sum(1 for o in objects if not o.get("vehicle_id"))
    by_vid = {m["vehicle_id"]: m for m in movements if m.get("vehicle_id")}
    meta = r.get("image_meta") or {}
    ts = datetime.fromisoformat(r["ts"])
    cap_min = ts.astimezone(timezone.utc).hour * 60 + ts.astimezone(timezone.utc).minute
    vehicles = []
    for o in objects:
        m = by_vid.get(o.get("vehicle_id") or "")
        state = "unknown" if m is None else ("approaching" if m.get("approaching") or m.get("within_base") else "other")
        trail = [[la, lo] for t, la, lo in tracks.get(o.get("vehicle_id") or "", []) if cap_min - TRAIL_MIN <= t <= cap_min]
        vehicles.append({"lat": o.get("lat"), "lon": o.get("lon"), "state": state, "label": CLASS_TR.get(o.get("cls"), o.get("cls")),
                         "eta_min": m.get("eta_min") if m else None, "trail": trail})
    llm = r.get("llm") or {}
    use_llm = bool(llm.get("rationale")) and llm.get("risk_level") == r["risk_level"]
    cc = meta.get("corner_coordinates") or {}
    footprint = [cc[k] for k in ("top_left", "top_right", "bottom_right", "bottom_left") if k in cc]
    boxes = []
    for o in objects:
        m = by_vid.get(o.get("vehicle_id") or "")
        b = o.get("bbox") or {}
        boxes.append({
            "x1": b.get("x1"), "y1": b.get("y1"), "x2": b.get("x2"), "y2": b.get("y2"),
            "label": CLASS_TR.get(o.get("cls"), o.get("cls")),
            "state": "unknown" if m is None else ("approaching" if m.get("approaching") or m.get("within_base") else "other"),
        })
    return {
        "image_id": r["image_id"],
        "image": f"images/{r['image_id']}.jpg",
        "width": meta.get("width_px"), "height": meta.get("height_px"),
        "capture_time": ts.astimezone(timezone.utc).strftime("%H:%M"),
        "capture_iso": ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "zone_id": r.get("zone_id"),
        "zone": ZONE_LABELS.get((r.get("zone_id") or "").lower(), r.get("zone_id")),
        "risk_level": r["risk_level"],
        "summary": plain_summary(movements, matched, len(objects)),
        "notes": plain_notes(untracked, incompatible),
        "rationale": r.get("rationale"),
        "explanation": display_text(llm["rationale"] if use_llm else (r.get("rationale") or "")),
        "explanation_source": "llm" if use_llm else "rule-based",
        "footprint": footprint,
        "map_vehicles": vehicles,
        "vehicles": len(objects),
        "approaching": sum(1 for m in movements if m.get("approaching") or m.get("within_base")),
        "boxes": boxes,
        "source": {"detection_id": r["detection_id"], "computed_at": r.get("computed_at"), "mode": "rule-based"},
    }


def copy_image(src: Path, dst: Path, max_w: int) -> None:
    try:
        from PIL import Image  # isteğe bağlı: mobilde daha hızlı yükleme
    except ImportError:
        shutil.copyfile(src, dst)
        return
    with Image.open(src) as im:
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        im.convert("RGB").save(dst, "JPEG", quality=82, optimize=True, progressive=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "web-ui/public/sim-data"))
    ap.add_argument("--images", default=str(ROOT / "data/REAL/images"))
    ap.add_argument("--psql", default=DEFAULT_PSQL, help="SQL'i stdin'den okuyup satır başına JSON basan psql komutu (-At)")
    ap.add_argument("--max-width", type=int, default=1280)
    ap.add_argument("--data", default=str(ROOT / "data/REAL"), help="zones.json + tracks.csv klasörü (harita)")
    ap.add_argument("--base-radius-m", type=float, default=1500.0, help="üs sınırı yarıçapı (compose BASE_RADIUS_M)")
    args = ap.parse_args()

    rows = fetch_rows(args.psql)
    if not rows:
        sys.exit("DB'de kural tabanlı değerlendirme yok — önce olayları kural tabanlı modda çalıştırın.")
    base, zones, tracks = load_map_data(Path(args.data), args.base_radius_m)
    events = [build_event(r, tracks) for r in rows]
    events.sort(key=lambda e: (e["capture_iso"], e["image_id"]))

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    missing = []
    for e in events:
        src = Path(args.images) / f"{e['image_id']}.jpg"
        if src.exists():
            copy_image(src, out / e["image"], args.max_width)
        else:
            missing.append(e["image_id"])
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "note": "Önceden hesaplanmış kural tabanlı sonuçlar (LLM kullanılmadı). Canlı sisteme bağlı değildir.",
        "base": base,
        "zones": zones,
        "events": events,
    }
    (out / "events.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    by_risk: dict[str, int] = {}
    for e in events:
        by_risk[e["risk_level"]] = by_risk.get(e["risk_level"], 0) + 1
    n_llm = sum(e["explanation_source"] == "llm" for e in events)
    print(f"{len(events)} olay -> {out / 'events.json'}; risk: {by_risk}; gerekçe: {n_llm} LLM, {len(events) - n_llm} kural tabanlı")
    if missing:
        print(f"UYARI: görüntüsü bulunamayan olaylar: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
