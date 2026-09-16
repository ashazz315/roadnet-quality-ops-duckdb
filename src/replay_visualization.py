"""Portable SVG comparison of one deterministic diagnostic case; no web UI."""

import json
from html import escape

from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, box
from shapely.ops import transform


def comparison_svg(rows, plans, tables, config):
    """Prefer the first turn anchor by issue ID, without filtering by its outcome."""
    if not rows:
        return None
    turn_ids = {
        plan["patch_id"]
        for plan in plans
        if plan["operation"] in {"add_no_turn", "remove_no_turn"}
    }
    row = next(
        (
            row
            for row in rows
            if row["patch_id"] in turn_ids and row["scenario_role"] == "anchor"
        ),
        rows[0],
    )
    nodes = {node["node_id"]: node for node in tables.road_node}
    project = Transformer.from_crs(4326, config["metric_crs"], always_xy=True).transform
    routes = {
        phase: json.loads(row[f"{phase}_route_json"]) for phase in ("before", "after")
    }
    geometries = {
        phase: transform(project, wkt.loads(route["geometry_wkt"]))
        for phase, route in routes.items()
        if route["geometry_wkt"]
    }
    endpoints = [
        project(nodes[row[key]]["longitude"], nodes[row[key]]["latitude"])
        for key in ("origin_node_id", "destination_node_id")
    ]
    bounds = [geometry.bounds for geometry in geometries.values()] or [
        LineString(endpoints).bounds
    ]
    left, bottom = min(b[0] for b in bounds) - 40, min(b[1] for b in bounds) - 40
    right, top = max(b[2] for b in bounds) + 40, max(b[3] for b in bounds) + 40
    span = max(right - left, top - bottom, 100)
    midx, midy = (left + right) / 2, (bottom + top) / 2
    left, bottom, right, top = (
        midx - span / 2,
        midy - span / 2,
        midx + span / 2,
        midy + span / 2,
    )
    extent = box(left, bottom, right, top)

    def xy(x, y):
        return (x - left) / span * 440 + 20, (top - y) / span * 440 + 20

    def polyline(line, color, width):
        points = " ".join(
            f"{x:.2f},{y:.2f}" for x, y in (xy(*point) for point in line.coords)
        )
        return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>'

    background = []
    for segment in tables.road_segment:
        clipped = transform(project, wkt.loads(segment["geometry_wkt"])).intersection(
            extent
        )
        lines = (
            [clipped]
            if clipped.geom_type == "LineString"
            else list(clipped.geoms)
            if clipped.geom_type == "MultiLineString"
            else []
        )
        background.extend(
            polyline(line, "#ccd5df", 1.5) for line in lines if not line.is_empty
        )
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="760" viewBox="0 0 1080 760" role="img" aria-labelledby="title desc">',
        '<title id="title">RoadInsight hypothetical route comparison</title>',
        '<desc id="desc">Same origin, destination and map scale. Before in blue, after in orange. Results are model estimates, not field verification.</desc>',
        '<rect width="1080" height="760" fill="#f5f7fb"/>',
        '<g font-family="Arial,sans-serif" fill="#17283d">',
        '<text x="40" y="42" font-size="24" font-weight="bold">RoadInsight / Route Replay</text>',
        '<text x="40" y="69" font-size="14">Hypothetical repair - same OD and routing parameters - not field verified</text>',
        f'<text x="40" y="93" font-size="12">Issue: {escape(row["issue_id"])}</text>',
    ]
    for index, phase in enumerate(("before", "after")):
        route = routes[phase]
        color, offset = (
            ("#2267c7" if phase == "before" else "#d26b13"),
            40 + index * 520,
        )
        label = (
            "BEFORE / current model"
            if phase == "before"
            else "AFTER / hypothetical model"
        )
        metric = (
            f"{route['distance_m']:.1f} m / model ETA {route['eta_s']:.1f} s"
            if route["distance_m"] is not None
            else "No available route / distance and ETA unavailable"
        )
        svg += [
            f'<text x="{offset}" y="132" font-size="17" font-weight="bold" fill="{color}">{label}</text>',
            f'<text x="{offset}" y="157" font-size="14">{metric}</text>',
            f'<g transform="translate({offset},177)">',
            '<rect width="480" height="480" rx="8" fill="white" stroke="#dce2e9"/>',
            *background,
        ]
        if phase in geometries:
            svg.append(polyline(geometries[phase], color, 4))
        for label, coords in zip(("O", "D"), endpoints, strict=True):
            x, y = xy(*coords)
            svg += [
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="9" fill="#17283d" stroke="white" stroke-width="2"/>',
                f'<text x="{x:.2f}" y="{y + 4:.2f}" text-anchor="middle" fill="white" font-size="10">{label}</text>',
            ]
        svg.append("</g>")
    delta = (
        "unavailable"
        if row["distance_delta_m"] is None
        else f"{row['distance_delta_m']:+.1f} m; model ETA {row['eta_delta_s']:+.1f} s"
    )
    legality = {True: "yes", False: "no", None: "unavailable"}[
        row["before_edge_sequence_legal_after"]
    ]
    svg += [
        f'<text x="40" y="690" font-size="15">After minus before: {delta}. Original edge sequence legal after: {legality}.</text>',
        '<text x="40" y="718" font-size="12">First turn anchor by issue ID; illustration is not selected for improvement. All ODs remain in route_replay.parquet.</text>',
        '<text x="40" y="739" font-size="11">Road background: OpenStreetMap contributors / ODbL. No traffic or real-world validation. North up; equal map scale.</text>',
        "</g></svg>",
    ]
    return "\n".join(svg)
