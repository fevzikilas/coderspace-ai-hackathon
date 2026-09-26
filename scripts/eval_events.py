#!/usr/bin/env python3
"""Veri setindeki TÜM olayları gateway üzerinden değerlendirir ve expected.json (tasarım niyeti) ile karşılaştırır.

  python scripts/eval_events.py [--gateway http://localhost:8000] [--api-key KEY] [--expected data/synthetic/expected.json]

Yalnızca standart kütüphane. Çıkış kodu: risk uyuşmazlığı varsa 1 (desen uyuşmazlığı yalnızca bilgi olarak raporlanır).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def call(url: str, key: str | None, body: dict | None = None, timeout: float = 180.0) -> tuple[int, dict]:
    headers = {"content-type": "application/json", **({"X-API-Key": key} if key else {})}
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--expected", default=str(root / "data/synthetic/expected.json"))
    ap.add_argument("--only-zone", default=None, help="yalnızca bu bölge adı (örn. 'Kuzey Yolu')")
    args = ap.parse_args()

    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    code, cat = call(f"{args.gateway}/events", args.api_key)
    if code != 200:
        print("GET /events başarısız:", code, cat)
        return 2
    events = cat["events"]
    print(f"{len(events)} olay; bölgeler: {len(cat['zones'])}; üs: {(cat.get('base') or {}).get('name')}\n")
    print(f"{'image_id':<11} {'saat':<5} {'bölge (çözülen)':<16} {'beklenen':<16} {'sistem':<16} {'mod':<11} sonuç")
    bad_risk = bad_pat = failed = 0
    by_risk: dict[str, int] = {}
    for ev in events:
        iid = ev["image_id"]
        exp = expected.get(iid)
        if exp is None or (args.only_zone and exp["zone"] != args.only_zone):
            continue
        code, run = call(f"{args.gateway}/pipeline/run", args.api_key, {"image_id": iid})
        res = run.get("result") or {}
        a = res.get("assessment")
        if run.get("status") != "succeeded" or a is None:
            failed += 1
            print(f"{iid:<11} {ev['capture_time']:<5} {ev.get('zone_name') or '-':<16} {exp['expected_risk']:<16} {'HATA':<16} {'-':<11} {run.get('error') or res.get('message')}")
            continue
        got_pat = (a.get("pattern") or {}).get("pattern") or "RANDOM"
        # sistemin tüm eşleşen desenleri içinde beklenen var mı (CONVOY + DIRECT_APPROACH birlikte olabilir)
        matched = (a.get("pattern") or {}).get("matched") or []
        pat_ok = exp["expected_pattern"] == got_pat or exp["expected_pattern"] in matched
        risk_ok = exp["expected_risk"] == a["risk_level"]
        bad_risk += not risk_ok
        bad_pat += not pat_ok
        by_risk[a["risk_level"]] = by_risk.get(a["risk_level"], 0) + 1
        zone_ok = (ev.get("zone_name") or "") == exp["zone"]
        flag = ("OK" if risk_ok else "RİSK≠") + ("" if pat_ok else " patern≠") + ("" if zone_ok else " bölge≠")
        print(f"{iid:<11} {ev['capture_time']:<5} {(ev.get('zone_name') or '-'):<16} {exp['expected_risk'] + '/' + exp['expected_pattern'][:8]:<16} {a['risk_level'] + '/' + got_pat[:8]:<16} {a['mode']:<11} {flag}")
    n = len(events)
    print(f"\nÖzet: {n} olay | çalışmayan: {failed} | risk uyuşmazlığı: {bad_risk} | patern uyuşmazlığı: {bad_pat} | dağılım: {by_risk}")
    return 1 if (bad_risk or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
