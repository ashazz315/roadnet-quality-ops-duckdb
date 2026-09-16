"""OSM normalization, road validation and directed graph construction (STEP 4).

Accept validated road segments, nodes, restrictions, and explicit versions.
Return graph/path objects without reading UI state or benchmark labels.
Implementations live in submodules to keep package imports lightweight.
Restriction-aware routing is reserved for STEP 7.
"""
