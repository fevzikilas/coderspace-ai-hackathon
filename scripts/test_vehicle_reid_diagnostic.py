from scripts.vehicle_reid_diagnostic import summarize_runs


def test_summarize_runs_reports_distribution_counts_without_accuracy_claims():
    runs = [
        {
            "result": {
                "candidate_vehicle_links": [
                    {"target_event_id": "e2", "target_track_id": "T2", "appearance_similarity": 0.87},
                    {"target_event_id": "e2", "target_track_id": "T2", "appearance_similarity": 0.91},
                    {"target_event_id": "e3", "target_track_id": "T3", "appearance_similarity": 0.83},
                ],
                "vehicle_link_diagnostics": {
                    "total_pairs": 12,
                    "accepted": 3,
                    "rejected_same_event": 1,
                    "rejected_temporal": 2,
                    "rejected_spatial": 2,
                    "rejected_similarity": 4,
                    "rejected_top_k": 5,
                },
            }
        }
    ]

    summary = summarize_runs(runs)

    assert summary["pairs"] == {
        "compared": 12,
        "accepted": 3,
        "rejected_same_event": 1,
        "rejected_temporal": 2,
        "rejected_spatial": 2,
        "rejected_similarity": 4,
        "rejected_top_k": 5,
    }
    assert summary["similarity"] == {"count": 3, "min": 0.83, "median": 0.87, "max": 0.91}
    assert summary["candidates_per_target_track"] == {"e2/T2": 2, "e3/T3": 1}
    assert summary["accuracy_metrics"] == "not_computed_without_ground_truth_identity"
