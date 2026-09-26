#!/usr/bin/env python3
"""LLM sağlayıcısına (glm | openrouter) ilk temas testi — yalnızca standart kütüphane, İSTEK-TASARRUFLU.

  python scripts/llm_smoke.py --provider openrouter                # anahtar .env'deki OPENROUTER_API_KEY'den okunur; 1 istek: model tool_calls döndürüyor mu?
  LLM_PROVIDER=openrouter OPENROUTER_API_KEY=sk-or-... python scripts/llm_smoke.py          # ya da ortam değişkeniyle
  LLM_PROVIDER=openrouter OPENROUTER_API_KEY=sk-or-... python scripts/llm_smoke.py --e2e img_003839 img_006673
  GLM_API_KEY=sk-... python scripts/llm_smoke.py --e2e img_003839                            # GLM (varsayılan sağlayıcı)

Adımlar: (1) [GLM] key/info harcama  (2) TEK istekle tool-calling sondası: model `tool_calls` döndürmüyorsa "UYGUN DEĞİL" denir, DURULUR, e2e denenmez.
(3) --e2e: en çok 2 gerçek olay (fazlası için --allow-more) gateway'den LLM moduyla değerlendirilir. Bu betik en çok 1 + 1 (+ olay başına ajan turu) istek harcar;
OpenRouter ücretsiz katman: 20/dk, 50/gün. Anahtar ASLA yazdırılmaz. reasoning_effort YALNIZCA glm'e gönderilir, `thinking` hiçbir zaman.
--e2e için risk-agent'ın aynı sağlayıcı/anahtarı görmesi gerekir: .env'de LLM_PROVIDER + anahtar, sonra `docker compose up -d risk-agent-svc`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

def _load_dotenv() -> None:
    """Depo kökündeki .env'i (git'e girmez) okur; ortamda zaten olan değişkenleri EZMEZ. Değerler asla yazdırılmaz."""
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if not os.path.isfile(f):
        return
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


if "--provider" in sys.argv:  # örn. --provider openrouter (.env'deki LLM_PROVIDER'ı bu çalıştırma için geçersiz kılar)
    os.environ["LLM_PROVIDER"] = sys.argv[sys.argv.index("--provider") + 1]
_load_dotenv()
PROVIDER = (os.getenv("LLM_PROVIDER") or "glm").strip().lower()
_GLM_BASE = "https://berriailitellm-databasev1826rc3-production-d691.up.railway.app/v1"
BASE = (os.getenv("LLM_BASE_URL") or (os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1" if PROVIDER == "openrouter" else os.getenv("GLM_BASE_URL") or _GLM_BASE)).rstrip("/")
MODEL = os.getenv("LLM_MODEL") or (os.getenv("OPENROUTER_MODEL") or "nvidia/nemotron-3-ultra-550b-a55b:free" if PROVIDER == "openrouter" else os.getenv("GLM_MODEL") or "glm-5.3-flash")
KEY = (os.getenv("LLM_API_KEY") or (os.getenv("OPENROUTER_API_KEY") if PROVIDER == "openrouter" else os.getenv("GLM_API_KEY")) or "").strip()
ROOT = os.getenv("GLM_ROOT_URL") or (BASE[:-3] if BASE.endswith("/v1") else BASE)
requests_made = 0


def call(method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 240.0) -> tuple[int, dict, float]:
    global requests_made
    h = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, h, method=method)
    t0 = time.time()
    if method == "POST" and "/chat/completions" in url:
        requests_made += 1
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r), time.time() - t0
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e), time.time() - t0
        except Exception:  # noqa: BLE001
            return e.code, {"raw": "<gövde okunamadı>"}, time.time() - t0


def glm_spend() -> tuple[float | None, float | None]:
    code, d, _ = call("GET", f"{ROOT}/key/info")
    if code != 200:
        print(f"   key/info HTTP {code}: {json.dumps(d)[:160]}")
        return None, None
    info = d.get("info", d)
    return info.get("spend"), info.get("max_budget")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["glm", "openrouter"], help="LLM_PROVIDER'ı bu çalıştırma için geçersiz kılar")
    ap.add_argument("--e2e", nargs="+", metavar="IMAGE_ID", help="en çok 2 gerçek olay (LLM moduyla)")
    ap.add_argument("--allow-more", action="store_true", help="2'den fazla olaya izin ver (ücretsiz kotayı yakabilir)")
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--gateway-key", default="dev-key-change-me")
    a = ap.parse_args()
    if not KEY:
        print(f"[{PROVIDER}] API anahtarı yok (OPENROUTER_API_KEY / GLM_API_KEY / LLM_API_KEY ortam değişkeni).")
        return 2
    if a.e2e and len(a.e2e) > 2 and not a.allow_more:
        print("Ücretsiz katman günlük kotasını korumak için en çok 2 olay; fazlası için --allow-more.")
        return 2
    print(f"sağlayıcı: {PROVIDER}\nbase: {BASE}\nmodel: {MODEL}\nreasoning_effort gönderilir mi: {PROVIDER == 'glm'}\n")

    s0 = mx = None
    if PROVIDER == "glm":
        print("1) bütçe (GET /key/info)")
        s0, mx = glm_spend()
        print(f"   harcanan: {s0} / üst sınır: {mx} USD\n")

    print("2) TEK istekle tool-calling sondası")
    # Ajanın gerçek araç biçimine yakın; argüman İSTEMDE AÇIKÇA verilir (belirsiz sonda modeli haksız yere elemesin)
    tools = [{"type": "function", "function": {"name": "get_movement_analysis", "description": "Bir aracın hız/yön/yaklaşma/ETA analizini döner", "parameters": {"type": "object", "properties": {"vehicle_id": {"type": "string", "description": "Araç kimliği, örn. T0109"}}, "required": ["vehicle_id"]}}}]
    body = {"model": MODEL, "messages": [{"role": "system", "content": "Bir üs koruma analistisin. Veri gerektiğinde ilgili aracı çağır; kendin tahmin yürütme."},
                                       {"role": "user", "content": "T0109 kimlikli aracın hareket analizini get_movement_analysis aracını çağırarak al."}],
            "tools": tools, "tool_choice": "auto", "max_tokens": 1500}
    if PROVIDER == "glm":
        body["reasoning_effort"] = "low"
    code, d, dt = call("POST", f"{BASE}/chat/completions", body)
    if code != 200:
        print(f"   HATA HTTP {code}: {json.dumps(d, ensure_ascii=False)[:400]}")
        return 1
    ch = (d.get("choices") or [{}])[0]; msg = ch.get("message") or {}; tc = msg.get("tool_calls")
    print(f"   {dt:.1f} sn | finish_reason={ch.get('finish_reason')} | usage={d.get('usage')}")
    print(f"   content={str(msg.get('content'))[:120]!r}")
    if not tc:
        print("\n   ✗ MODEL tool_calls DÖNDÜRMEDİ (düz metin). Bu model agent tool-calling döngüsü için UYGUN DEĞİL — e2e denenmiyor.")
        return 3
    fn = tc[0].get("function", {})
    print(f"   ✓ tool_calls: {fn.get('name')}({fn.get('arguments')})  [toplam {len(tc)} çağrı]")
    try:
        json.loads(fn.get("arguments") or "{}")
        print("   ✓ arguments geçerli JSON")
    except json.JSONDecodeError:
        print("   ⚠ arguments geçerli JSON DEĞİL — ajan bu çağrıyı işleyemez")
        return 3

    if a.e2e:
        for iid in a.e2e:
            print(f"\n3) gerçek olay, LLM modu: {iid}")
            code, r, dt = call("POST", f"{a.gateway}/pipeline/run", {"image_id": iid}, {"X-API-Key": a.gateway_key}, timeout=900)
            asm = ((r.get("result") or {}).get("assessment")) or {}
            log = asm.get("tool_calls_log") or []
            print(f"   HTTP {code} | {dt:.1f} sn | durum={r.get('status')} | mod={asm.get('mode')} (beklenen: llm) | model={asm.get('model')} | risk={asm.get('risk_level')} (kendi veri: {asm.get('own_data_level')})")
            print(f"   fallback_reason={asm.get('fallback_reason')} | araç çağrıları: {[(x.get('tool'), x.get('status')) for x in log if x.get('origin') == 'llm'][:12]} | kullanım={asm.get('usage')}")
            print(f"   politika düzeltmeleri: {asm.get('policy_adjustments')}")
            print(f"   bütçe: { {k: v for k, v in (asm.get('budget') or {}).items() if k in ('requests_today', 'max_requests_per_day', 'requests_last_minute')} }")
            for e in asm.get("evidence_breakdown", []):
                print(f"     {e['source']:<9} w={e['weight']:<5} {e['effect']:<8} {e['summary'][:110]}")
            print(f"   gerekçe: {str(asm.get('rationale'))[:350]}")
    if PROVIDER == "glm":
        s1, _ = glm_spend()
        if s0 is not None and s1 is not None:
            print(f"\nBu testin maliyeti: ~{s1 - s0:.4f} USD (toplam {s1} / {mx})")
    print(f"\nBu betiğin attığı sohbet isteği sayısı: {requests_made} (olay başına ajan turları risk-agent tarafında ayrıca sayılır)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
