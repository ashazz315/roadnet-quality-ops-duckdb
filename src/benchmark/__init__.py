"""Evaluation-only boundary, reserved for STEP 5 and STEP 9.

Own the golden snapshot, fault injection, expected labels and evaluation.
Keep answers separate from detector inputs; the golden network is a controlled
benchmark baseline, not independently verified reality. Compute metrics from
matched expected/detected objects and an explicit evaluation universe.
Fault injection and evaluation metrics are not implemented in STEP 3.
"""
