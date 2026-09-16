"""Observable candidate construction shared by the four rule detectors."""

from __future__ import annotations

import hashlib
import json


def time_bins(refs, rules):
    return {
        int(row["start_time"].timestamp() // (3600 * rules["time_bin_hours"]))
        for row in refs
    }


def enough(refs, rules, minimum=None):
    return (
        len({row["trajectory_id"] for row in refs})
        >= (minimum or rules["minimum_trajectories"])
        and len(time_bins(refs, rules)) >= rules["minimum_time_bins"]
    )


def candidate(
    kind,
    object_type,
    object_id,
    geometry,
    network,
    refs,
    reports,
    metrics,
    hypothesis,
    action,
    *,
    structural=1.0,
    contradiction=0.0,
    score_cap=1.0,
):
    location = network.wgs_geometry(geometry)
    point = location.centroid
    references = [
        {
            key: row[key]
            for key in (
                "trajectory_id",
                "start_seq",
                "end_seq",
                "start_time",
                "end_time",
                "quality",
            )
        }
        for row in refs
    ]
    return {
        "issue_type": str(kind),
        "object_type": object_type,
        "object_id": object_id,
        "longitude": point.x,
        "latitude": point.y,
        "geometry_wkt": location.wkt,
        "trajectory_refs": references,
        "feedback_refs": [
            {
                "feedback_id": row["feedback_id"],
                "input_row_id": int(row["input_row_id"]),
            }
            for row in reports
        ],
        "metrics": metrics,
        "root_cause_hypothesis": hypothesis,
        "suggested_action": action,
        "topology_support": structural,
        "contradiction_fraction": contradiction,
        "confidence_cap": score_cap,
    }


def stable_id(prefix, value):
    return (
        prefix
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:20]
    )
