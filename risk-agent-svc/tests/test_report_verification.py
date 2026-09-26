"""Rapor↔tespit/iz niceliksel karşılaştırması: risk-agent tarafı (core-svc /verify-claims sonuçlarını kullanır)."""
from __future__ import annotations

import json

import httpx

from .conftest import World, glm_reply, tool_call
from .test_agent import script

V = {  # core-svc'nin döndüreceği doğrulama sonuçları (id -> sonuç)
    "r001": {"id": "r001", "verdict": "compatible", "checks": [{"aspect": "konum", "claimed": "araç", "observed": "V-101 12 m'de", "result": "match"}, {"aspect": "hareket", "claimed": "üsse doğru ilerliyor", "observed": "9.0 m/s", "result": "match"}, {"aspect": "kimlik", "claimed": "dost/teyitli", "observed": "doğrulanamaz", "result": "unverifiable"}], "unverified": ["kimlik"], "summary": "UYUMLU: ✓ konum; ✓ hareket: rapor üsse doğru ilerliyor / bulgu 9.0 m/s; ? kimlik (Tarif edilen araç gerçekten üsse yaklaşıyor; 'dost' iddiası bunu değiştirmez.)", "nearest_track_m": 12.0},
    "r002": {"id": "r002", "verdict": "incompatible", "checks": [{"aspect": "sayı", "claimed": "5 araç", "observed": "1 araç", "result": "mismatch"}], "unverified": [], "summary": "UYUMSUZ: ✗ sayı: rapor 5 araç / bulgu 1 araç", "nearest_track_m": 20.0},
    "r003": {"id": "r003", "verdict": "irrelevant", "checks": [], "unverified": [], "summary": "İLGİSİZ: hava durumu"},
    "r004": {"id": "r004", "verdict": "unverifiable", "checks": [{"aspect": "konum", "claimed": "araç", "observed": "iz yok", "result": "unverifiable"}], "unverified": ["konum"], "summary": "DOĞRULANAMADI: ? konum"},
}


def _item(i, text, source, claim, loc=True):
    return {"id": f"r{i:03d}", "text": text, "source": source, "time": "13:05", "ts": "2026-01-01T00:00:00Z", "age_min": 5.0, "distance_m": 100.0,
            "location": {"lat": 39.88, "lon": 32.77} if loc else None, "claim": claim}


ITEMS = [
    _item(1, "39.88N 32.77E konumundan usse dogru ilerleyen otomobil planli ikmal aracidir, kimlik teyidi yapilmistir.", "official", {"kind": "vehicle", "types": ["car"], "motion": "toward_base", "identity": "friendly"}),
    _item(2, "39.88N 32.77E cevresinde 5 kamyon bulundugu yonunde ihbar alindi.", "third_party", {"kind": "vehicle", "types": ["truck"], "count": 5, "count_relation": "exact", "hearsay": True}),
    _item(3, "Hava acik, gorus mesafesi iyi.", "official", {"kind": "context", "context_reason": "hava durumu"}, loc=False),
    _item(4, "Bir kaynak, 39.9N 32.8E konumunda agir bir aracin beklemede oldugunu iletti.", "third_party", {"kind": "vehicle", "types": ["truck", "bus"], "motion": "stationary"}),
]


class VWorld(World):
    def __init__(self, *a, verify_status: int = 200, items=None, **kw):
        super().__init__(*a, **kw)
        self.verify_status, self.items, self.verify_body = verify_status, items or ITEMS, None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/reports/ZONE-ALPHA":
            self.calls.append(f"{request.method} {path}")
            return httpx.Response(200, json={"zone_id": "ZONE-ALPHA", "source": "dataset", "items": self.items})
        if path == "/detections/det-1/verify-claims":
            self.calls.append(f"{request.method} {path}")
            self.verify_body = json.loads(request.content)
            if self.verify_status != 200:
                return httpx.Response(self.verify_status, json={"detail": "core hatası"})
            return httpx.Response(200, json={"results": [V[c["id"]] for c in self.verify_body["claims"]]})
        return super().handler(request)


async def test_rule_based_evidence_uses_quantitative_verification_not_keywords(make_harness):
    w = VWorld()
    h = make_harness(w, None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    ev = next(e for e in a["evidence_breakdown"] if e["source"] == "reports")
    assert "1 uyumlu, 1 uyumsuz, 1 doğrulanamadı, 1 ilgisiz" in ev["summary"]
    assert "Uyumsuz örnek" in ev["summary"] and "sayı: rapor 5 araç / bulgu 1 araç" in ev["summary"] and "esas alınmadı" in ev["summary"] and "UYUMSUZ:" not in ev["summary"]
    rv = a["report_verification"]
    assert rv["by_verdict"] == {"compatible": 1, "incompatible": 1, "irrelevant": 1, "unverifiable": 1}
    assert [i["verdict"] for i in rv["items"]] == ["incompatible", "compatible", "unverifiable"]  # ilgisiz listelenmez
    assert "1 uyumlu, 1 uyumsuz" in a["rationale"]
    # verify isteği: konum + yapılandırılmış iddia gitti
    body = w.verify_body
    assert body["base_location"]["lat"] == a["base"]["lat"] and body["claims"][0]["claim"]["motion"] == "toward_base" and body["claims"][0]["lat"] == 39.88


async def test_friendly_claim_about_an_approaching_vehicle_does_not_lower_risk(make_harness):
    h = make_harness(VWorld(), None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["risk_level"] == "HIGH" and a["own_data_level"] == "HIGH"
    ev = next(e for e in a["evidence_breakdown"] if e["source"] == "reports")
    assert "kimlik/dostluk iddiası" in ev["summary"] and "riski düşürmez" in ev["summary"] and "gerçekten üsse yaklaşıyor" in ev["summary"]
    assert ev["effect"] == "raises"  # uyumlu rapor yaklaşan araç tarif ediyor ve kendi verimiz zaten HIGH
    assert ev["trust"] == "low" and ev["weight"] <= 0.2


async def test_verification_outage_degrades_gracefully_to_unverified_reports(make_harness):
    h = make_harness(VWorld(verify_status=500), None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    ev = next(e for e in a["evidence_breakdown"] if e["source"] == "reports")
    assert "karşılaştırılamadı" in ev["summary"] and a["report_verification"] is None
    assert a["risk_level"] == "HIGH"


async def test_llm_tool_result_carries_verification_sorted_and_trimmed(make_harness):
    from .test_agent import full_round, submit

    g = script(full_round(), glm_reply([tool_call("get_reports", {"zone_id": "ZONE-ALPHA"}, "r1")]), glm_reply([submit("HIGH")]))
    h = make_harness(VWorld(), g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "llm"
    tool_msg = next(m for m in g.seen[-1]["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == "r1")
    res = json.loads(tool_msg["content"])
    assert res["verification_summary"]["by_verdict"]["incompatible"] == 1 and "irrelevant" not in [i["verification"]["verdict"] for i in res["items"]]
    assert [i["verification"]["verdict"] for i in res["items"]] == ["incompatible", "compatible", "unverifiable"]
    assert res["omitted"]["irrelevant"] == 1 and "DÜŞÜRMEZ" in res["verification_legend"]
    assert res["verification_summary"]["identity_claims_describing_an_approaching_vehicle"] == 1


async def test_legacy_reports_without_claims_still_work(make_harness):
    h = make_harness(World(), None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    ev = next(e for e in a["evidence_breakdown"] if e["source"] == "reports")
    assert "karşılaştırılamadı" in ev["summary"] and a["report_verification"] is None
