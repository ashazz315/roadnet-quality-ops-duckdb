"""Before/after hypothetical route replay boundary, implemented in STEP 7.

Use explicit before/fixed network snapshots, the same origin/destination and
routing parameters. Return route status, geometry, distance and modelled ETA
with versions. An unreachable route is not a zero-length trip. A valid fix may
increase distance/time; never guarantee improvement or modify original inputs.
Pure modules receive current inputs and issues; no evaluation answers or I/O.
"""
