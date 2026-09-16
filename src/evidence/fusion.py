"""Deduplicate observable support and expose denominators and source references."""


def fuse(candidate, rules, valid_fraction):
    references = {
        (row["trajectory_id"], row["start_seq"], row["end_seq"]): row
        for row in candidate["trajectory_refs"]
    }
    refs = [references[key] for key in sorted(references)]
    reports = {row["feedback_id"]: row for row in candidate["feedback_refs"]}
    by_trip = {}
    for row in refs:
        by_trip.setdefault(row["trajectory_id"], []).append(row["quality"])
    count = len(by_trip)
    quality = (
        sum(sum(values) / len(values) for values in by_trip.values()) / count
        if count
        else 0.0
    )
    bins = {
        int(row["start_time"].timestamp() // (3600 * rules["time_bin_hours"]))
        for row in refs
    }
    dimensions = {
        "data_quality": quality * valid_fraction,
        "trajectory_support": min(count / rules["support_saturation"], 1.0),
        "topology_support": candidate["topology_support"],
        "feedback_support": min(len(reports) / rules["feedback_saturation"], 1.0),
        "persistence": min(len(bins) / rules["persistence_saturation"], 1.0),
    }
    return {
        "dimensions": dimensions,
        "trajectory_refs": refs,
        "feedback_refs": [reports[key] for key in sorted(reports)],
        "trajectory_count": count,
        "feedback_count": len(reports),
        "time_bin_count": len(bins),
        "contradiction_fraction": candidate["contradiction_fraction"],
        "confidence_cap": candidate["confidence_cap"],
        "first_observed_at": min(row["start_time"] for row in refs).isoformat(),
        "last_observed_at": max(row["end_time"] for row in refs).isoformat(),
    }
