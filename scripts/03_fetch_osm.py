"""Fetch one immutable public OSM snapshot; not part of the offline test suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_sources.osm import fetch_osm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/osm_xuhui.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", help="Explicit alternative Overpass endpoint; recorded in source.json")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.endpoint:
        config["overpass_endpoint"] = args.endpoint
    metadata = fetch_osm(config, args.output)
    print(json.dumps({key: metadata[key] for key in ("raw_sha256", "osm_timestamp", "retrieved_at")}, indent=2))


if __name__ == "__main__":
    main()
