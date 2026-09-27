from __future__ import annotations

from app.vehicle_reid import VehicleEvidenceIndex


def observation(
    event: str,
    track: str,
    timestamp: str,
    embedding: list[float],
    *,
    lat: float | None = 39.9,
    lon: float | None = 32.75,
) -> dict:
    return {
        "event_id": event,
        "track_id": track,
        "timestamp": timestamp,
        "position": None if lat is None or lon is None else {"lat": lat, "lon": lon},
        "class": "car",
        "crop": {"image_id": event, "bbox": {"x1": 1, "y1": 2, "x2": 20, "y2": 24}},
        "quality": {"score": 0.8},
        "model": "test-model",
        "embedding": embedding,
    }


def index(**overrides) -> VehicleEvidenceIndex:
    return VehicleEvidenceIndex(
        min_similarity=overrides.get("min_similarity", 0.8),
        top_k=overrides.get("top_k", 2),
        max_temporal_gap_s=overrides.get("max_temporal_gap_s", 3600),
        max_implied_speed_mps=overrides.get("max_implied_speed_mps", 70),
        max_observations=overrides.get("max_observations", 20),
    )


def test_same_event_tracks_are_never_candidates():
    store = index(min_similarity=0.0)
    store.add_and_match([observation("event-a", "T1", "2025-06-01T10:00:00Z", [1.0, 0.0])])
    links, graph, stats = store.add_and_match(
        [observation("event-a", "T2", "2025-06-01T10:05:00Z", [1.0, 0.0])]
    )

    assert links == []
    assert graph["edges"] == []
    assert stats["rejected_same_event"] == 1


def test_temporal_order_gap_and_impossible_spatial_speed_are_rejected():
    store = index(min_similarity=0.0, max_temporal_gap_s=600, max_implied_speed_mps=50)
    store.add_and_match(
        [
            observation("future", "TF", "2025-06-01T10:10:00Z", [1.0, 0.0]),
            observation("old", "TO", "2025-06-01T09:00:00Z", [1.0, 0.0]),
            observation("far", "TX", "2025-06-01T10:00:00Z", [1.0, 0.0], lat=40.9, lon=32.75),
        ]
    )

    links, _, stats = store.add_and_match(
        [observation("current", "TC", "2025-06-01T10:05:00Z", [1.0, 0.0])]
    )

    assert links == []
    assert stats["rejected_temporal"] == 2
    assert stats["rejected_spatial"] == 1


def test_minimum_similarity_and_top_k_are_applied_per_target_track():
    store = index(min_similarity=0.75, top_k=2)
    store.add_and_match(
        [
            observation("e1", "T1", "2025-06-01T10:00:00Z", [1.0, 0.0]),
            observation("e2", "T2", "2025-06-01T10:01:00Z", [0.95, 0.05]),
            observation("e3", "T3", "2025-06-01T10:02:00Z", [0.8, 0.2]),
            observation("e4", "T4", "2025-06-01T10:03:00Z", [0.0, 1.0]),
        ]
    )

    links, graph, stats = store.add_and_match(
        [observation("now", "TN", "2025-06-01T10:05:00Z", [1.0, 0.0])]
    )

    assert len(links) == 2
    assert [link["source_track_id"] for link in links] == ["T1", "T2"]
    assert all(link["relation"] == "POSSIBLE_SAME_VEHICLE" for link in links)
    assert all(link["feasibility"] == {"temporal": True, "spatial": True, "spatial_checked": True} for link in links)
    assert len(graph["edges"]) == 2
    assert stats["rejected_similarity"] == 1
    assert stats["accepted"] == 2


def test_missing_coordinates_are_marked_unchecked_not_treated_as_road_feasibility():
    store = index(min_similarity=0.0)
    store.add_and_match([observation("e1", "T1", "2025-06-01T10:00:00Z", [1.0], lat=None, lon=None)])

    links, _, _ = store.add_and_match(
        [observation("e2", "T2", "2025-06-01T10:05:00Z", [1.0])]
    )

    assert len(links) == 1
    assert links[0]["spatial_distance_m"] is None
    assert links[0]["implied_speed_mps"] is None
    assert links[0]["feasibility"] == {"temporal": True, "spatial": True, "spatial_checked": False}


def test_edges_do_not_create_global_or_transitive_identity_clusters():
    store = index(min_similarity=0.7, top_k=1)
    store.add_and_match([observation("a", "A", "2025-06-01T10:00:00Z", [1.0, 0.0])])
    links_b, _, _ = store.add_and_match([observation("b", "B", "2025-06-01T10:05:00Z", [0.9, 0.1])])
    links_c, graph_c, _ = store.add_and_match([observation("c", "C", "2025-06-01T10:10:00Z", [0.8, 0.2])])

    assert len(links_b) == 1 and len(links_c) == 1
    assert links_c[0]["source_track_id"] == "B"
    assert "clusters" not in graph_c
    assert "identity_id" not in graph_c["nodes"][0]
    repeat = index(min_similarity=0.7, top_k=1)
    repeat.add_and_match([observation("a", "A", "2025-06-01T10:00:00Z", [1.0, 0.0])])
    repeat.add_and_match([observation("b", "B", "2025-06-01T10:05:00Z", [0.9, 0.1])])
    repeated_links, _, _ = repeat.add_and_match(
        [observation("c", "C", "2025-06-01T10:10:00Z", [0.8, 0.2])]
    )
    assert links_c[0]["link_id"].startswith("vl-")
    assert links_c[0]["link_id"] == repeated_links[0]["link_id"]
