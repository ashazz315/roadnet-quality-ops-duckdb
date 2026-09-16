"""Uncalibrated evidence score, explicitly separate from correctness probability."""

import math


def score_confidence(evidence, rules):
    dimensions = evidence["dimensions"]
    if any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in dimensions.values()
    ):
        raise ValueError("Evidence dimensions must be finite fractions")
    contributions = {
        key: dimensions[key] * weight
        for key, weight in rules["confidence_weights"].items()
    }
    contradiction = evidence["contradiction_fraction"]
    cap = evidence["confidence_cap"]
    if not 0 <= contradiction <= 1 or not 0 <= cap <= 1:
        raise ValueError("Invalid confidence penalty or cap")
    raw = sum(contributions.values())
    score = min(cap, raw * (1 - 0.5 * contradiction))
    return {
        "confidence": round(score, 6),
        "confidence_kind": "uncalibrated_evidence_score",
        "calibrated": False,
        "contributions": contributions,
        "raw_score": raw,
        "contradiction_multiplier": 1 - 0.5 * contradiction,
        "cap": cap,
    }
