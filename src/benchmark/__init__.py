"""Evaluation-only boundary: STEP 5 generation; metrics reserved for STEP 9.

Own the golden snapshot, fault injection, expected labels and evaluation.
Keep answers separate from detector inputs; the golden network is a controlled
benchmark baseline, not independently verified reality. Compute metrics from
matched expected/detected objects and an explicit evaluation universe.
Fault injection and synthetic observations live here; detectors must not import
this package. Evaluation metrics are not implemented yet.
"""
