"""Session-scoped cross-event vehicle appearance evidence.

Edges are independent candidate links. They never merge track identities and have no
transitive semantics.
"""
from __future__ import annotations

import hashlib
import math
from collections import deque
from datetime import datetime
from typing import Any


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _haversine_m(a: dict[str, float], b: dict[str, float]) -> float:
    radius = 6_371_008.8
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlon = math.radians(b["lon"] - a["lon"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def _cosine(a: list[float], b: list[float]) -> float | None:
    if not a or len(a) != len(b):
        return None
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return None
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _link_id(source: dict[str, Any], target: dict[str, Any]) -> str:
    raw = f"{source['event_id']}|{source['track_id']}|{target['event_id']}|{target['track_id']}"
    return "vl-" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def _node(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": f"{observation['event_id']}::{observation['track_id']}",
        "event_id": observation["event_id"],
        "track_id": observation["track_id"],
        "timestamp": observation["timestamp"],
        "position": observation.get("position"),
        "class": observation.get("class"),
        "crop": observation.get("crop"),
        "crop_quality": observation.get("quality"),
    }


class VehicleEvidenceIndex:
    def __init__(
        self,
        *,
        min_similarity: float,
        top_k: int,
        max_temporal_gap_s: float,
        max_implied_speed_mps: float,
        max_observations: int,
    ) -> None:
        self.min_similarity = min(max(float(min_similarity), -1.0), 1.0)
        self.top_k = max(1, int(top_k))
        self.max_temporal_gap_s = max(0.0, float(max_temporal_gap_s))
        self.max_implied_speed_mps = max(0.0, float(max_implied_speed_mps))
        self._observations: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_observations)))

    def add_and_match(self, current: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
        stats = {
            "total_pairs": 0,
            "accepted": 0,
            "rejected_same_event": 0,
            "rejected_temporal": 0,
            "rejected_spatial": 0,
            "rejected_similarity": 0,
            "rejected_top_k": 0,
        }
        feasible: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        # A rerun replaces the same event/track observation. This prevents repeated
        # investigations from manufacturing duplicate evidence edges.
        current_by_key = {(item["event_id"], item["track_id"]): item for item in current}
        current_unique = list(current_by_key.values())
        prior = [
            item for item in self._observations
            if (item["event_id"], item["track_id"]) not in current_by_key
        ]
        for incoming in current_unique:
            target_candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
            for stored in prior:
                stats["total_pairs"] += 1
                if stored["event_id"] == incoming["event_id"]:
                    stats["rejected_same_event"] += 1
                    continue
                try:
                    stored_time = _time(stored["timestamp"])
                    incoming_time = _time(incoming["timestamp"])
                except (TypeError, ValueError):
                    stats["rejected_temporal"] += 1
                    continue
                # Investigation order is not evidence order. Always orient the link
                # from the chronologically earlier observation to the later one.
                if stored_time < incoming_time:
                    source, target = stored, incoming
                elif incoming_time < stored_time:
                    source, target = incoming, stored
                else:
                    stats["rejected_temporal"] += 1
                    continue
                gap = abs((incoming_time - stored_time).total_seconds())
                if gap <= 0 or gap > self.max_temporal_gap_s:
                    stats["rejected_temporal"] += 1
                    continue

                source_pos, target_pos = source.get("position"), target.get("position")
                distance: float | None = None
                implied_speed: float | None = None
                spatial_checked = source_pos is not None and target_pos is not None
                if spatial_checked:
                    distance = _haversine_m(source_pos, target_pos)
                    implied_speed = distance / gap
                    if implied_speed > self.max_implied_speed_mps:
                        stats["rejected_spatial"] += 1
                        continue

                similarity = _cosine(source.get("embedding") or [], target.get("embedding") or [])
                if similarity is None or similarity < self.min_similarity:
                    stats["rejected_similarity"] += 1
                    continue
                link = {
                    "link_id": _link_id(source, target),
                    "source_event_id": source["event_id"],
                    "source_track_id": source["track_id"],
                    "target_event_id": target["event_id"],
                    "target_track_id": target["track_id"],
                    "relation": "POSSIBLE_SAME_VEHICLE",
                    "appearance_similarity": round(similarity, 4),
                    "temporal_gap_seconds": int(round(gap)),
                    "spatial_distance_m": None if distance is None else round(distance, 1),
                    "implied_speed_mps": None if implied_speed is None else round(implied_speed, 2),
                    "feasibility": {"temporal": True, "spatial": True, "spatial_checked": spatial_checked},
                    "evidence": {
                        "source_crop": source.get("crop"),
                        "target_crop": target.get("crop"),
                        "source_quality": source.get("quality"),
                        "target_quality": target.get("quality"),
                        "model": target.get("model") if source.get("model") == target.get("model") else [source.get("model"), target.get("model")],
                    },
                }
                target_candidates.append((link, source, target))
            feasible.extend(target_candidates)

        # Sparse demo graph: enforce top-K degree at BOTH endpoints. Edges remain
        # independent candidates; this is readability control, not clustering.
        feasible.sort(key=lambda item: (-item[0]["appearance_similarity"], item[0]["temporal_gap_seconds"], item[0]["link_id"]))
        accepted: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        degree: dict[str, int] = {}
        for item in feasible:
            link = item[0]
            source_key = f"{link['source_event_id']}::{link['source_track_id']}"
            target_key = f"{link['target_event_id']}::{link['target_track_id']}"
            if degree.get(source_key, 0) >= self.top_k or degree.get(target_key, 0) >= self.top_k:
                stats["rejected_top_k"] += 1
                continue
            degree[source_key] = degree.get(source_key, 0) + 1
            degree[target_key] = degree.get(target_key, 0) + 1
            accepted.append(item)

        self._observations = deque(
            [*prior, *current_unique],
            maxlen=self._observations.maxlen,
        )

        links = [item[0] for item in accepted]
        stats["accepted"] = len(links)
        nodes_by_id = {_node(item)["node_id"]: _node(item) for item in current_unique}
        for _, source, target in accepted:
            for item in (source, target):
                node = _node(item)
                nodes_by_id[node["node_id"]] = node
        graph = {
            "relation_semantics": "candidate_edges_are_independent_not_identity_clusters",
            "nodes": sorted(nodes_by_id.values(), key=lambda item: (item["timestamp"], item["event_id"], item["track_id"])),
            "edges": links,
        }
        return links, graph, stats
