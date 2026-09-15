"""Before/after route replay boundary, reserved for STEP 7.

Use explicit before/fixed network snapshots, the same origin/destination and
routing parameters. Return route status, geometry, distance and modelled ETA
with versions. An unreachable route is not a zero-length trip. A valid fix may
increase distance/time; never guarantee improvement or modify original inputs.
Route replay is not implemented in STEP 3.
"""
