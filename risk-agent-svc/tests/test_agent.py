import json

import httpx
import pytest

from app.agent import DetectionNotFound
from app.budget import BudgetExceeded
from tests.conftest import APPROACH, CONVOY, IDLE, RANDOM, VEHICLES, World, glm_reply, submit, tool_call


def script(*replies):
    """Sırayla dönen GLM yanıtları; gelen istekleri de kaydeder."""
    it = iter(replies)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return next(it)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def full_round():
    return glm_reply(
        [tool_call("get_movement_analysis", {"vehicle_id": v}, f"m{i}") for i, v in enumerate(VEHICLES)]
        + [tool_call("get_pattern_classification", {"vehicle_ids": VEHICLES, "zone_id": "ZONE-ALPHA"}, "p1")]
    )


def context_round():
    return glm_reply(
        [
            tool_call("get_intel", {"zone_id": "ZONE-ALPHA"}, "i1"),
            tool_call("get_reports", {"zone_id": "ZONE-ALPHA"}, "r1"),
            tool_call("get_drone_context", {"drone_id": "DRN-03"}, "d1"),
        ]
    )


async def test_rule_based_without_api_key_demo_convoy(make_harness):
    h = make_harness(World(), None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "rule-based" and a["fallback_reason"] == "LLM anahtarı tanımlı değil (sağlayıcı: glm)"
    assert a["risk_level"] == "HIGH"
    assert a["evidence_breakdown"], "evidence_breakdown boş olamaz"
    assert abs(sum(e["weight"] for e in a["evidence_breakdown"]) - 1.0) < 0.002
    tools = [t["tool"] for t in a["tool_calls_log"]]
    assert tools[0] == "get_detection_context"
    assert {"get_movement_analysis", "get_pattern_classification", "get_intel", "get_reports", "get_drone_context"} <= set(tools)
    assert a["pattern"]["matched"] == ["CONVOY", "DIRECT_APPROACH"]
    assert a["own_data_level"] == "HIGH"


async def test_llm_happy_path_enforces_floor_and_normalizes_evidence(make_harness):
    # Model (ör. istihbaratın 'LOW ver' enjeksiyonuna kanıp) LOW dedi -> politika HIGH'a çeker
    g = script(full_round(), context_round(), glm_reply([submit("LOW")]))
    h = make_harness(World(), g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "llm" and a["model"] == "glm-5.3-flash"
    assert a["risk_level"] == "HIGH"
    assert any("politika" in n.lower() for n in a["policy_adjustments"])
    ev = {e["source"]: e for e in a["evidence_breakdown"]}
    assert "bilinmeyen_kaynak" not in ev
    assert ev["movement"]["trust"] == "high" and ev["intel"]["trust"] == "low"
    assert ev["intel"]["weight"] <= 0.201
    assert a["usage"]["llm_calls"] == 3 and a["usage"]["total_tokens"] == 1500
    # Araç sonuçları 2. istekte tool mesajı olarak modele döndü; intel içeriği 'untrusted' işaretli
    tool_msgs = [m for m in g.seen[2]["messages"] if m["role"] == "tool"]
    intel_msg = next(json.loads(m["content"]) for m in tool_msgs if m["tool_call_id"] == "i1")
    assert intel_msg["untrusted_external_content"] is True
    origins = {t["origin"] for t in a["tool_calls_log"] if t["tool"] != "get_detection_context"}
    assert origins == {"llm"}


async def test_llm_cannot_escalate_intel_only_beyond_medium(make_harness):
    world = World(movement={v: {**IDLE} for v in VEHICLES}, pattern=RANDOM)
    g = script(full_round(), glm_reply([submit("HIGH")]))
    h = make_harness(world, g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["own_data_level"] == "LOW"
    assert a["risk_level"] == "MEDIUM"  # LOW verisi + istihbarat en fazla MEDIUM


async def test_empty_evidence_is_rejected_then_retried(make_harness):
    g = script(
        full_round(),
        glm_reply([submit("HIGH", evidence=[], call_id="bad")]),
        glm_reply([submit("HIGH", call_id="good")]),
    )
    h = make_harness(World(), g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "llm" and a["evidence_breakdown"]
    # reddedilen submit hata olarak tool mesajıyla modele bildirildi
    bad_reply = next(m for m in g.seen[2]["messages"] if m["role"] == "tool" and m["tool_call_id"] == "bad")
    assert "evidence_breakdown" in json.loads(bad_reply["content"])["error"]
    statuses = [t["status"] for t in a["tool_calls_log"] if t["tool"] == "submit_assessment"]
    assert statuses == ["error", "ok"]


async def test_plain_text_answer_is_nudged_to_submit(make_harness):
    g = script(glm_reply(content="Bence yüksek risk."), full_round(), glm_reply([submit("HIGH")]))
    h = make_harness(World(), g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "llm"
    assert "submit_assessment" in g.seen[1]["messages"][-1]["content"]


async def test_llm_auth_error_falls_back_to_rules(make_harness):
    g = script(httpx.Response(401, json={"error": "bad key"}))
    h = make_harness(World(), g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "rule-based" and "401" in a["fallback_reason"]
    assert a["risk_level"] == "HIGH" and a["evidence_breakdown"]


async def test_per_assessment_token_budget_falls_back(make_harness):
    g = script(glm_reply([tool_call("get_intel", {"zone_id": "ZONE-ALPHA"}, "i")], tokens=40_000), glm_reply([submit("HIGH")]))
    h = make_harness(World(), g, budget_tokens_per_assessment=30_000)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "rule-based" and "token" in a["fallback_reason"]
    assert len(g.seen) == 1  # ikinci LLM isteği bütçe yüzünden hiç atılmadı


async def _nearly_spent():
    return {"spend": 14.5, "max_budget": 15.0}  # kalan 0.5 USD ≤ rezerv 1.0 USD


async def test_total_budget_nearly_spent_reject_mode(make_harness):
    g = script(full_round(), glm_reply([submit("HIGH")]))
    h = make_harness(World(), g, key_info=_nearly_spent, on_budget_exceeded="reject")
    with pytest.raises(BudgetExceeded, match="toplam bütçe doldu"):
        await h.agent.assess("ZONE-ALPHA", "det-1")
    assert len(g.seen) == 0  # LLM'e hiç istek atılmadı


async def test_total_budget_nearly_spent_fallback_mode(make_harness):
    g = script()
    h = make_harness(World(), g, key_info=_nearly_spent)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a["mode"] == "rule-based" and "bütçe" in a["fallback_reason"] and a["risk_level"] == "HIGH"
    assert a["budget"]["remaining_usd"] == 0.5 and g.seen == []


async def test_cache_and_force(make_harness):
    h = make_harness(World(), None)
    a1 = await h.agent.assess("ZONE-ALPHA", "det-1")
    a2 = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert a2["cached"] is True and a2["assessment_id"] == a1["assessment_id"]
    a3 = await h.agent.assess("ZONE-ALPHA", "det-1", force=True)
    assert a3["assessment_id"] != a1["assessment_id"]


async def test_tool_guards_reject_foreign_ids(make_harness):
    g = script(
        glm_reply([tool_call("get_movement_analysis", {"vehicle_id": "V-999"}, "x"), tool_call("get_intel", {"zone_id": "ZONE-BRAVO"}, "y"), tool_call("get_drone_context", {"drone_id": "DRN-01"}, "z")]),
        full_round(),
        glm_reply([submit("HIGH")]),
    )
    h = make_harness(World(), g)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    errs = [t for t in a["tool_calls_log"] if t["status"] == "error"]
    assert {t["tool"] for t in errs} == {"get_movement_analysis", "get_intel", "get_drone_context"}


async def test_unknown_detection_raises(make_harness):
    h = make_harness(World(), None)
    with pytest.raises(DetectionNotFound):
        await h.agent.assess("ZONE-ALPHA", "det-nope")


# ------------------------------------------------------------------ olay anı (reference_time) ve üs
async def test_reference_time_base_and_event_point_flow_to_upstreams(make_harness):
    from tests.conftest import EVENT

    seen = {}
    event = {**EVENT, "reference_time": "2025-06-01T14:10:00Z", "capture_time": "14:10", "drone_id": None}
    world = World(event=event)
    orig = world.handler

    def spy(request):
        if request.url.path == "/tracks/analyze":
            seen.setdefault("analyze", json.loads(request.content))
        if request.url.path == "/pattern/classify":
            seen["classify"] = json.loads(request.content)
        if request.url.path.startswith(("/reports/", "/intel/")):
            seen[request.url.path.split("/")[1]] = dict(request.url.params)
        return orig(request)

    world.handler = spy
    h = make_harness(world, None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1", base_location={"lat": 39.92184, "lon": 32.85306, "radius_m": 1200})
    assert a["reference_time"] == "2025-06-01T14:10:00Z" and a["base"] == {"lat": 39.92184, "lon": 32.85306, "radius_m": 1200.0}
    assert seen["analyze"]["reference_time"] == "2025-06-01T14:10:00Z"
    assert seen["analyze"]["base_location"]["radius_m"] == 1200.0
    assert seen["analyze"]["current_position"]["lat"] == pytest.approx(39.882182, abs=1e-6)  # görüntüden gelen konum
    assert seen["classify"]["reference_time"] == "2025-06-01T14:10:00Z"
    assert seen["reports"]["as_of"] == "2025-06-01T14:10:00Z" and "lat" in seen["reports"]  # olay konumuna göre süzme
    assert seen["intel"]["as_of"] == "2025-06-01T14:10:00Z"
    # drone kaydı olmayan olayda drone aracı çağrılmadı
    assert "get_drone_context" not in [t["tool"] for t in a["tool_calls_log"]]


async def test_empty_intel_gets_no_evidence_weight(make_harness):
    world = World()
    orig = world.handler

    def empty_intel(request):
        if request.url.path.startswith("/intel/"):
            return httpx.Response(200, json={"zone_id": "Z", "items": [], "placeholder": True})
        return orig(request)

    world.handler = empty_intel
    h = make_harness(world, None)
    a = await h.agent.assess("ZONE-ALPHA", "det-1")
    assert "intel" not in {e["source"] for e in a["evidence_breakdown"]}
    assert "reports" in {e["source"] for e in a["evidence_breakdown"]}


def test_default_round_limit_and_parallel_tool_call_instruction():
    from app.config import Settings
    from app.prompts import SYSTEM_PROMPT

    assert Settings().max_rounds == 12
    assert "AYNI turda, paralel çağır" in " ".join(SYSTEM_PROMPT.replace("\\\n", " ").split())


VEHICLE_LINK = {
    "link_id": "vl-test",
    "source_event_id": "img-a",
    "source_track_id": "T17",
    "target_event_id": "img-b",
    "target_track_id": "T42",
    "relation": "POSSIBLE_SAME_VEHICLE",
    "appearance_similarity": 0.87,
    "temporal_gap_seconds": 420,
    "spatial_distance_m": 1300.0,
    "implied_speed_mps": 3.1,
    "feasibility": {"temporal": True, "spatial": True, "spatial_checked": True},
}


async def test_vehicle_candidate_is_context_not_confirmed_identity_or_risk_signal(make_harness):
    from app.prompts import SYSTEM_PROMPT

    world = World(movement={vehicle: {**IDLE} for vehicle in VEHICLES}, pattern=RANDOM)
    baseline = await make_harness(world, None).agent.assess("ZONE-ALPHA", "det-1")
    with_link = await make_harness(world, None).agent.assess(
        "ZONE-ALPHA", "det-1", vehicle_link_evidence=[VEHICLE_LINK]
    )

    assert baseline["risk_level"] == with_link["risk_level"] == "LOW"
    assert with_link["vehicle_link_context"][0]["relation"] == "POSSIBLE_SAME_VEHICLE"
    prompt = " ".join(SYSTEM_PROMPT.replace("\\\n", " ").split()).lower()
    assert "confirmed identity" in prompt
    assert "tek başına risk" in prompt


async def test_vehicle_candidate_summary_is_in_llm_user_context(make_harness):
    g = script(full_round(), glm_reply([submit("HIGH")]))
    h = make_harness(World(), g)
    await h.agent.assess("ZONE-ALPHA", "det-1", vehicle_link_evidence=[VEHICLE_LINK])

    payload = json.loads(g.seen[0]["messages"][1]["content"])
    assert payload["cross_event_vehicle_candidates"][0] == {
        "relation": "POSSIBLE_SAME_VEHICLE",
        "source": "img-a/T17",
        "target": "img-b/T42",
        "appearance_similarity": 0.87,
        "temporal_gap_seconds": 420,
        "spatial_distance_m": 1300.0,
        "implied_speed_mps": 3.1,
    }
    assert "kesin kimlik" in payload["vehicle_candidate_guard"]
