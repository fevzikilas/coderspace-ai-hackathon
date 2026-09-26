#!/usr/bin/env python3
"""Sentetik olay veri setini üretir (deterministik).

  python scripts/gen_dataset.py [--scenarios data/synthetic/scenarios.yaml] [--out data/synthetic]

Girdi : scenarios.yaml (8 bölge x 5 olay; hareket senaryoları, saha raporları, beklenen sonuçlar)
Çıktı : zones.json · image_meta.json · tracks.csv · field_reports.json  (kullanıcının örnek şemalarıyla aynı)
        images/img_XXXXXX.jpg  (960x540 sentetik hava görüntüsü)
        ground_truth.json      (görüntüdeki araçların GERÇEK bbox'ları; gerçek YOLO modeli gelene kadar mock dedektörü besler)
        expected.json          (olay başına tasarım niyeti: desen + risk; scripts/eval_events.py ile karşılaştırılır)

Koordinat dönüşümü, çekirdek georeferansla AYNI (resmî) formülü kullanır — boylam üst kenardan, enlem sol kenardan, doğrusal —, bu yüzden
ground-truth bbox merkezi georeferanslanınca aracın gerçek konumuna ~0.1 m'de düşer.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter

R = 6371008.8
STEP_S = 300.0
# sınıf -> (uzunluk m, genişlik m, gövde rengi)
VEHICLE = {
    "pickup": (5.3, 2.0, (170, 60, 50)),
    "truck": (8.5, 2.5, (70, 90, 60)),
    "car": (4.4, 1.8, (200, 200, 205)),
    "van": (5.5, 2.0, (230, 230, 225)),
    "bus": (12.0, 2.5, (220, 170, 40)),
    "armored_vehicle": (7.0, 3.0, (60, 70, 55)),
}


# ---------------------------------------------------------------------------------------------- coğrafya
def dest(lat, lon, bearing, dist):
    b, d = math.radians(bearing), dist / R
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def enu(lat0, lon0, lat, lon):
    return math.radians(lon - lon0) * R * math.cos(math.radians(lat0)), math.radians(lat - lat0) * R


def from_enu(lat0, lon0, e, n):
    return lat0 + math.degrees(n / R), lon0 + math.degrees(e / (R * math.cos(math.radians(lat0))))


def hhmm(minute: int) -> str:
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"


def minutes(clock: str) -> int:
    h, m = clock.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------------------------------------- hareket modelleri
def simulate(zone: dict, cfg: dict, base: tuple[float, float], rng: random.Random, last_min: int) -> dict[str, list[dict]]:
    """Araç başına 5 dk'lık adımlarla [{minute, lat, lon, heading}] (GERÇEK konum; GPS gürültüsü sonra eklenir)."""
    m = zone["motion"]
    bearing = zone["bearing_deg"]
    start = minutes(m["start"])
    step_min = cfg["step_min"]
    n_steps = (last_min - start) // step_min + 1
    out: dict[str, list[dict]] = {}
    vehicles = zone["vehicles"]

    for idx, veh in enumerate(vehicles):
        pts: list[dict] = []
        kind = m["type"]
        if kind == "approach":
            v = m["speed_mps"] * STEP_S
            stop = m.get("stop_at_m")
            heading = (bearing + 180) % 360  # üsse doğru; durunca da aynı yön korunur
            for k in range(n_steps):
                d = m["from_m"] - v * k
                if stop is not None:
                    d = max(stop, d)
                d += m.get("spacing_m", 0) * idx  # kolonda geride (üsse daha uzak)
                lat, lon = dest(*base, bearing, d)
                pts.append({"minute": start + k * step_min, "lat": lat, "lon": lon, "heading": heading})
        elif kind == "outbound":
            course = bearing + m.get("course_offset_deg", 0.0)
            p0 = dest(*base, bearing, m["from_m"])
            for k in range(n_steps):
                lat, lon = dest(*p0, course, m["speed_mps"] * STEP_S * k)
                lat, lon = dest(lat, lon, course + 180, m.get("spacing_m", 0) * idx)
                pts.append({"minute": start + k * step_min, "lat": lat, "lon": lon, "heading": course % 360})
        elif kind == "loiter":
            clat, clon = dest(*base, bearing, m["dist_m"])
            e = nn = 0.0
            sigma = m["radius_m"] / 3.0
            for k in range(n_steps):
                e, nn = 0.8 * e + rng.gauss(0, sigma * 0.6), 0.8 * nn + rng.gauss(0, sigma * 0.6)
                lat, lon = from_enu(clat, clon, e, nn)
                pts.append({"minute": start + k * step_min, "lat": lat, "lon": lon, "heading": rng.uniform(0, 360) if k == 0 else pts[-1]["heading"]})
        elif kind == "random_walk":
            clat, clon = dest(*base, bearing, m["dist_m"] + idx * 600)
            e, nn = rng.uniform(-300, 300), rng.uniform(-300, 300)
            heading = rng.uniform(0, 360)
            for k in range(n_steps):
                lat, lon = from_enu(clat, clon, e, nn)
                pts.append({"minute": start + k * step_min, "lat": lat, "lon": lon, "heading": heading})
                heading = rng.uniform(0, 360)  # her adımda bağımsız yön: doğrudan yaklaşma/kafile imkânsız
                step = m["speed_mps"] * STEP_S * rng.uniform(0.15, 0.6)
                ne, nn2 = e + step * math.sin(math.radians(heading)), nn + step * math.cos(math.radians(heading))
                if math.hypot(ne, nn2) > m["radius_m"]:  # merkeze doğru çek
                    heading = math.degrees(math.atan2(-e, -nn)) % 360
                    ne, nn2 = e + step * math.sin(math.radians(heading)), nn + step * math.cos(math.radians(heading))
                e, nn = ne, nn2
        else:
            raise SystemExit(f"bilinmeyen hareket türü: {kind}")
        out[veh["track"]] = pts
    return out


# ---------------------------------------------------------------------------------------------- görüntü
def render(path: Path, W: int, H: int, gsd: float, boxes_px: list[dict], heading_hint: float, road_px: tuple[float, float], rng: random.Random, terrain: tuple[int, int, int]) -> None:
    img = Image.new("RGB", (W, H), terrain)
    d = ImageDraw.Draw(img)
    for _ in range(90):  # tarla parçaları
        x, y = rng.randint(-100, W), rng.randint(-100, H)
        w, h = rng.randint(80, 300), rng.randint(60, 200)
        t = rng.randint(-14, 14)
        d.rectangle([x, y, x + w, y + h], fill=tuple(max(0, min(255, c + t)) for c in terrain))
    for _ in range(420):  # çalılık/gürültü
        x, y, r = rng.randint(0, W), rng.randint(0, H), rng.randint(2, 7)
        t = rng.randint(-25, 18)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(max(0, min(255, c + t)) for c in terrain))
    # yol: araçların hareket doğrultusunda, ARAÇLARIN gerçek hattından (road_px) geçen şerit
    a = math.radians(heading_hint)
    ux, uy = math.sin(a), -math.cos(a)  # ekranda ileri yönü (kuzey yukarı)
    cx, cy = road_px
    L = math.hypot(W, H)
    road_w = 7.0 / gsd
    nx, ny = -uy, ux
    pts = [(cx - ux * L + nx * road_w / 2, cy - uy * L + ny * road_w / 2), (cx + ux * L + nx * road_w / 2, cy + uy * L + ny * road_w / 2),
           (cx + ux * L - nx * road_w / 2, cy + uy * L - ny * road_w / 2), (cx - ux * L - nx * road_w / 2, cy - uy * L - ny * road_w / 2)]
    d.polygon(pts, fill=(88, 88, 92))
    for i in range(-30, 30):  # kesikli orta çizgi
        s0 = i * 60
        d.line([(cx + ux * s0, cy + uy * s0), (cx + ux * (s0 + 28), cy + uy * (s0 + 28))], fill=(200, 190, 120), width=2)
    for b in boxes_px:
        # gölge + gövde + kabin
        sh = [(x + 4, y + 5) for x, y in b["poly"]]
        d.polygon(sh, fill=(30, 34, 30))
        d.polygon(b["poly"], fill=b["color"], outline=(20, 20, 20))
        cab = b["cab"]
        d.polygon(cab, fill=tuple(int(c * 0.45) for c in b["color"]))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(path, "JPEG", quality=88)


def vehicle_polys(e_m: float, n_m: float, heading: float, cls: str, W: int, H: int, gsd: float):
    """(merkeze göre doğu/kuzey m) konumdaki aracın piksel poligonu, kabin poligonu ve eksene paralel bbox'ı."""
    L, Wd, color = VEHICLE[cls]
    h = math.radians(heading)
    f = (math.sin(h), math.cos(h))  # ileri (doğu, kuzey)
    r = (math.cos(h), -math.sin(h))  # sağ

    def px(de, dn):
        return (W / 2 + (e_m + de) / gsd, H / 2 - (n_m + dn) / gsd)

    def corner(sf, sr, ls=L, ws=Wd):
        return px(f[0] * ls / 2 * sf + r[0] * ws / 2 * sr, f[1] * ls / 2 * sf + r[1] * ws / 2 * sr)

    poly = [corner(1, 1), corner(1, -1), corner(-1, -1), corner(-1, 1)]
    cab = [corner(0.55, 0.8, ls=L * 0.9, ws=Wd), corner(0.55, -0.8, ls=L * 0.9, ws=Wd), corner(0.05, -0.8, ls=L * 0.9, ws=Wd), corner(0.05, 0.8, ls=L * 0.9, ws=Wd)]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return poly, cab, (min(xs), min(ys), max(xs), max(ys)), color


# ---------------------------------------------------------------------------------------------- ana akış
def main() -> None:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--scenarios", default=str(root / "data/synthetic/scenarios.yaml"))
    ap.add_argument("--out", default=str(root / "data/synthetic"))
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.scenarios).read_text(encoding="utf-8"))
    ds = cfg["dataset"]
    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    rng = random.Random(ds["seed"])
    base = (ds["base"]["lat"], ds["base"]["lon"])
    W, H = ds["image"]["width_px"], ds["image"]["height_px"]
    foot_w = ds["image"]["footprint_width_m"]
    foot_h = foot_w * H / W
    gsd = foot_w / W
    noise = ds["gps_noise_m"]

    zones_json = {"base": {"name": ds["base"]["name"], "lat": ds["base"]["lat"], "lon": ds["base"]["lon"]}, "zones": []}
    tracks_rows: list[tuple[str, int, float, float]] = []
    image_meta: dict = {}
    ground_truth: dict = {}
    expected: dict = {}
    reports: list[dict] = []
    img_no = ds["first_image_number"]
    terrains = [(96, 116, 78), (110, 118, 82), (128, 116, 84), (104, 112, 76), (118, 122, 88), (98, 110, 84), (124, 112, 80), (108, 120, 90)]

    for zi, zone in enumerate(cfg["zones"]):
        zlat, zlon = dest(*base, zone["bearing_deg"], ds["zone_ring_km"] * 1000)
        zones_json["zones"].append({"name": zone["name"], "center": [round(zlat, 6), round(zlon, 6)]})
        last_min = max(minutes(e["time"]) for e in zone["events"])
        sim = simulate(zone, ds, base, rng, last_min)
        by_min = {tid: {p["minute"]: p for p in pts} for tid, pts in sim.items()}

        # ---- tracks.csv (GPS gürültülü); yalnızca son olay anına kadar
        for tid, pts in sim.items():
            for p in pts:
                ne, nn = rng.gauss(0, noise), rng.gauss(0, noise)
                lat, lon = from_enu(p["lat"], p["lon"], ne, nn)
                tracks_rows.append((tid, p["minute"], lat, lon))

        # ---- saha raporları
        first_tid = zone["vehicles"][0]["track"]

        def fill(text: str) -> str:
            def pos(mt):
                mm = minutes(mt.group(1))
                p = by_min[first_tid].get(mm) or by_min[first_tid][min(by_min[first_tid], key=lambda k: abs(k - mm))]
                return f"{p['lat']:.4f}N {p['lon']:.4f}E"

            def ring(mt):
                lat, lon = dest(*base, float(mt.group(1)), float(mt.group(2)))
                return f"{lat:.4f}N {lon:.4f}E"

            text = re.sub(r"\{pos@(\d\d:\d\d)\}", pos, text)
            return re.sub(r"\{ring@(\d+):(\d+)\}", ring, text)

        for rp in zone.get("reports", []):
            reports.append({"time": rp["time"], "source": rp["source"], "text": fill(rp["text"])})

        # ---- olaylar (görüntüler)
        for ev in zone["events"]:
            mm = minutes(ev["time"])
            subjects = ev.get("subjects") or [v["track"] for v in zone["vehicles"]]
            cls_of = {v["track"]: v["class"] for v in zone["vehicles"]}
            pos_now = {t: by_min[t][mm] for t in subjects}
            clat = sum(p["lat"] for p in pos_now.values()) / len(pos_now)
            clon = sum(p["lon"] for p in pos_now.values()) / len(pos_now)
            jitter = ds["image"]["center_jitter_m"]
            clat, clon = from_enu(clat, clon, rng.uniform(-jitter, jitter), rng.uniform(-jitter, jitter))
            dlat, dlon = foot_h / 2 / 111320.0, foot_w / 2 / (111320.0 * math.cos(math.radians(clat)))
            tl = (round(clat + dlat, 6), round(clon - dlon, 6))
            tr = (round(clat + dlat, 6), round(clon + dlon, 6))
            bl = (round(clat - dlat, 6), round(clon - dlon, 6))
            br = (round(clat - dlat, 6), round(clon + dlon, 6))
            img_no += rng.randint(7, 37)
            image_id = f"img_{img_no:06d}"

            boxes_px, gt_boxes = [], []
            for t in subjects:
                p = pos_now[t]
                e_m, n_m = enu(clat, clon, p["lat"], p["lon"])
                poly, cab, bb, color = vehicle_polys(e_m, n_m, p["heading"], cls_of[t], W, H, gsd)
                if bb[0] < 10 or bb[1] < 10 or bb[2] > W - 10 or bb[3] > H - 10:
                    raise SystemExit(f"{image_id}: {t} kadraj dışında/kenarda ({bb}); scenarios.yaml aralıklarını daraltın")
                boxes_px.append({"poly": poly, "cab": cab, "color": color})
                gt_boxes.append({"class": cls_of[t], "conf": round(rng.uniform(0.86, 0.97), 2), "x1": round(bb[0], 1), "y1": round(bb[1], 1), "x2": round(bb[2], 1), "y2": round(bb[3], 1), "track": t})

            if ev.get("distractor"):  # izsiz park halinde sivil araç
                for _ in range(50):
                    de, dn = rng.uniform(-foot_w / 2 + 12, foot_w / 2 - 12), rng.uniform(-foot_h / 2 + 8, foot_h / 2 - 8)
                    if all(math.hypot(de - enu(clat, clon, pos_now[t]["lat"], pos_now[t]["lon"])[0], dn - enu(clat, clon, pos_now[t]["lat"], pos_now[t]["lon"])[1]) > 25 for t in subjects):
                        poly, cab, bb, color = vehicle_polys(de, dn, rng.uniform(0, 360), "car", W, H, gsd)
                        if bb[0] > 10 and bb[1] > 10 and bb[2] < W - 10 and bb[3] < H - 10:
                            boxes_px.append({"poly": poly, "cab": cab, "color": (150, 155, 165)})
                            gt_boxes.append({"class": "car", "conf": round(rng.uniform(0.78, 0.9), 2), "x1": round(bb[0], 1), "y1": round(bb[1], 1), "x2": round(bb[2], 1), "y2": round(bb[3], 1), "track": None})
                            break

            offs = [enu(clat, clon, p["lat"], p["lon"]) for p in pos_now.values()]
            road_px = (W / 2 + sum(o[0] for o in offs) / len(offs) / gsd, H / 2 - sum(o[1] for o in offs) / len(offs) / gsd)
            render(out / "images" / f"{image_id}.jpg", W, H, gsd, boxes_px, pos_now[subjects[0]]["heading"], road_px, random.Random(f"{ds['seed']}-{image_id}"), terrains[zi % len(terrains)])
            image_meta[image_id] = {
                "width_px": W, "height_px": H, "capture_time": ev["time"],
                "corner_coordinates": {"top_left": list(tl), "top_right": list(tr), "bottom_left": list(bl), "bottom_right": list(br)},
            }
            ground_truth[image_id] = {"width_px": W, "height_px": H, "boxes": gt_boxes}
            expected[image_id] = {"zone": zone["name"], "capture_time": ev["time"], "scenario": zone["scenario"], "subjects": subjects,
                                  "n_objects": len(gt_boxes), "expected_pattern": ev["expect"]["pattern"], "expected_risk": ev["expect"]["risk"]}

    reports += [dict(r) for r in cfg.get("general_reports", [])]
    reports.sort(key=lambda r: minutes(r["time"]))

    # ---- dosyaları yaz (kullanıcının örnek şemalarıyla aynı biçim; geçerli JSON/CSV)
    def dump(name: str, obj) -> None:
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dump("zones.json", zones_json)
    dump("image_meta.json", image_meta)
    dump("field_reports.json", reports)
    dump("ground_truth.json", ground_truth)
    dump("expected.json", expected)
    tracks_rows.sort(key=lambda r: (r[0], r[1]))
    (out / "tracks.csv").write_text("track_id,time,lat,lon\n" + "".join(f"{t},{hhmm(m)},{lat:.6f},{lon:.6f}\n" for t, m, lat, lon in tracks_rows), encoding="utf-8")
    print(f"üretildi: {len(image_meta)} görüntü, {len(set(r[0] for r in tracks_rows))} iz ({len(tracks_rows)} nokta), {len(reports)} saha raporu, {len(zones_json['zones'])} bölge -> {out}")


if __name__ == "__main__":
    main()
