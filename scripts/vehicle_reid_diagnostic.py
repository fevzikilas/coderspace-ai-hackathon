#!/usr/bin/env python3
"""Summarize exported vehicle-link run results without claiming identity accuracy."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    pair_keys = (
        "total_pairs",
        "accepted",
        "rejected_same_event",
        "rejected_temporal",
        "rejected_spatial",
        "rejected_similarity",
    )
    totals = Counter({key: 0 for key in pair_keys})
    similarities: list[float] = []
    per_target: Counter[str] = Counter()
    for run in runs:
        result = run.get("result") or {}
        diagnostic = result.get("vehicle_link_diagnostics") or {}
        for key in pair_keys:
            totals[key] += int(diagnostic.get(key, 0) or 0)
        for link in result.get("candidate_vehicle_links") or []:
            similarities.append(float(link["appearance_similarity"]))
            per_target[f"{link['target_event_id']}/{link['target_track_id']}"] += 1
    similarities.sort()
    distribution = {
        "count": len(similarities),
        "min": round(similarities[0], 4) if similarities else None,
        "median": round(statistics.median(similarities), 4) if similarities else None,
        "max": round(similarities[-1], 4) if similarities else None,
    }
    return {
        "pairs": {
            "compared": totals["total_pairs"],
            "accepted": totals["accepted"],
            "rejected_same_event": totals["rejected_same_event"],
            "rejected_temporal": totals["rejected_temporal"],
            "rejected_spatial": totals["rejected_spatial"],
            "rejected_similarity": totals["rejected_similarity"],
        },
        "similarity": distribution,
        "candidates_per_target_track": dict(sorted(per_target.items())),
        "accuracy_metrics": "not_computed_without_ground_truth_identity",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON: full pipeline run list or {'runs': [...]} export")
    parser.add_argument("--output", type=Path, help="Write JSON here instead of stdout")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    runs = payload.get("runs", []) if isinstance(payload, dict) else payload
    if not isinstance(runs, list):
        raise SystemExit("input must be a run list or an object containing runs[]")
    rendered = json.dumps(summarize_runs(runs), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
