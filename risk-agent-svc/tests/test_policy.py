from app import policy
from app.facts import Facts
from tests.conftest import APPROACH, CONVOY, EVENT, IDLE, RANDOM, VEHICLES

BASE = {"lat": 39.9, "lon": 32.75, "radius_m": 1500.0}


def facts(movement, pattern):
    f = Facts(zone_id="ZONE-ALPHA", detection=EVENT, base=BASE)
    f.movement = {v: movement for v in VEHICLES}
    f.pattern = pattern
    return f


def test_convoy_approach_is_high():
    own = policy.own_data_level(facts(APPROACH, CONVOY))
    assert own.idx == 2 and own.convoy_bumped is False  # hareket zaten HIGH -> bump gerekmez
    assert any("CONVOY" in r for r in own.reasons)


def test_convoy_bumps_low_movement_one_tier():
    far_static = {**IDLE, "distance_to_base_m": 12000.0}
    convoy_only = {**CONVOY, "detail": {**CONVOY["detail"], "matched_patterns": [CONVOY["detail"]["matched_patterns"][0]]}}
    own = policy.own_data_level(facts(far_static, convoy_only))
    assert own.base_idx == 0 and own.idx == 1 and own.convoy_bumped is True


def test_random_static_far_is_low():
    own = policy.own_data_level(facts({**IDLE, "distance_to_base_m": 12000.0}, RANDOM))
    assert own.idx == 0


def test_enforce_cannot_downgrade_and_caps_upgrade():
    own = policy.own_data_level(facts(APPROACH, CONVOY))  # HIGH
    assert policy.enforce(0, own)[0] == 2  # LLM LOW dedi -> HIGH'a çekilir
    low_own = policy.own_data_level(facts({**IDLE, "distance_to_base_m": 12000.0}, RANDOM))  # LOW
    final, notes = policy.enforce(2, low_own)  # LLM istihbarata dayanıp HIGH dedi
    assert final == 1 and notes


def test_insufficient_data_flag():
    f = facts({**IDLE, "insufficient_data": True, "distance_to_base_m": None}, RANDOM)
    own = policy.own_data_level(f)
    assert own.idx == 0 and own.insufficient


def test_normalize_evidence_caps_low_trust_and_drops_unknown():
    raw = [
        {"source": "movement", "weight": 0.2, "summary": "a", "trust": "low"},
        {"source": "intel", "weight": 0.6, "summary": "b"},
        {"source": "reports", "weight": 0.2, "summary": "c"},
        {"source": "xyz", "weight": 0.9, "summary": "d"},
    ]
    ev = policy.normalize_evidence(raw)
    assert {e["source"] for e in ev} == {"movement", "intel", "reports"}
    assert abs(sum(e["weight"] for e in ev) - 1.0) < 0.002
    assert sum(e["weight"] for e in ev if e["trust"] == "low") <= 0.201
    assert next(e for e in ev if e["source"] == "movement")["trust"] == "high"  # LLM'in 'low'u ezildi


def test_normalize_evidence_rejects_empty():
    assert policy.normalize_evidence([]) == []
    assert policy.normalize_evidence([{"source": "movement", "weight": 1, "summary": ""}]) == []
    assert policy.normalize_evidence("x") == []
