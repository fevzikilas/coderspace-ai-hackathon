"""İzsiz (track'siz) tespitler: risk-agent'a gider ama HAREKET VERİSİ YOK diye açıkça işaretlenir; sessizce LOW'a/zararsıza düşmez."""
import json

from .conftest import EVENT, World, glm_reply, tool_call
from .test_agent import full_round, script, submit

UNTRACKED = {"class": "van", "conf": 0.81, "lat": 39.8815, "lon": 32.7742, "vehicle_id": None, "box_index": 3}
EVENT_WITH_GAP = {**EVENT, "detections": [*EVENT["detections"], UNTRACKED]}


async def test_untracked_detection_is_flagged_and_does_not_dilute_risk(make_harness):
    h = make_harness(World(event=EVENT_WITH_GAP), None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["risk_level"] == "HIGH"  # izsiz nesne riski düşürmez
    g = a["data_gaps"]
    assert g["untracked_detections"] == 1 and g["untracked_classes"] == {"van": 1} and "hareket verisi YOK" in g["note"] and "LOW/zararsız varsayılmadı" in g["note"]
    det_ev = next(e for e in a["evidence_breakdown"] if e["source"] == "detection")
    assert "1 nesne izsiz: hareket verisi YOK" in det_ev["summary"] and "LOW varsayılmadı" in det_ev["summary"]
    assert "Veri boşluğu" in a["rationale"] and any(r.startswith("Veri boşluğu") for r in a["own_data_reasons"])


async def test_no_gap_means_no_data_gaps_field(make_harness):
    a = await make_harness(World(), None).agent.assess("ZONE-ALPHA", "det-1")
    assert a["data_gaps"] is None and not any("Veri boşluğu" in r for r in a["own_data_reasons"])


async def test_vehicles_beyond_the_analysis_limit_are_reported_not_silently_dropped(make_harness):
    h = make_harness(World(), None, max_vehicles=2)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["data_gaps"]["vehicles_over_limit"] == ["V-103"] and "AGENT_MAX_VEHICLES" in a["data_gaps"]["note"]
    assert a["vehicle_ids"] == ["V-101", "V-102"]


async def test_llm_sees_untracked_objects_marked_and_the_gap_is_always_in_the_rationale(make_harness):
    g = script(full_round(), glm_reply([submit("HIGH")]))
    h = make_harness(World(event=EVENT_WITH_GAP), g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    user = json.loads(next(m for m in g.seen[0]["messages"] if m["role"] == "user")["content"])
    flags = {d["box_index"] if "box_index" in d else i: d for i, d in enumerate(user["tespitler"])}
    assert [d["iz_kaydi"] for d in user["tespitler"]] == [True, True, True, False]
    assert user["tespitler"][3]["hareket_verisi"] == "YOK (izsiz nesne)" and "hareket_verisi" not in user["tespitler"][0]
    assert user["veri_bosluklari"]["untracked_detections"] == 1 and flags
    assert a["mode"] == "llm" and "(Veri boşluğu:" in a["rationale"]  # LLM anmasa da deterministik eklenir
