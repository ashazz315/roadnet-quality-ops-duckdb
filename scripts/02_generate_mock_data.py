"""Generate auditable simulated issue points from real reference roads."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.simulation import (  # noqa: E402
    generate_simulated_records,
    load_reference_roads,
    write_csv,
    write_geojson,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate simulated issue points from real road geometries.")
    parser.add_argument("--roads", type=Path, default=Path("data/reference/roads.geojson"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--issue-rate", type=float, default=0.7)
    parser.add_argument("--offset-share", type=float, default=0.2)
    parser.add_argument("--offset-min-m", type=float, default=15.0)
    parser.add_argument("--offset-max-m", type=float, default=40.0)
    args = parser.parse_args()

    roads = load_reference_roads(args.roads)
    records = generate_simulated_records(
        roads,
        count=args.count,
        seed=args.seed,
        issue_rate=args.issue_rate,
        offset_share=args.offset_share,
        offset_min_m=args.offset_min_m,
        offset_max_m=args.offset_max_m,
    )
    geojson_path = args.output_dir / "simulated_records.geojson"
    csv_path = args.output_dir / "simulated_records.csv"
    write_geojson(geojson_path, records)
    write_csv(csv_path, records)

    issue_count = sum(bool(record["properties"]["expected_is_issue"]) for record in records)
    offset_count = sum(record["properties"]["expected_issue_type"] == "point_offset" for record in records)
    print(f"Loaded reference roads: {len(roads)} from {args.roads}")
    print(f"Generated records: {len(records)}; issues: {issue_count}; offsets: {offset_count}")
    print(f"GeoJSON: {geojson_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
