"""Back up a stopped workspace; refuses if another server owns its volume."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from roadinsight_ui.workspace import Workspace

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (args.data_dir / "workspace.sqlite").is_file():
        parser.error("No existing workspace.sqlite found")
    workspace = Workspace(args.data_dir)
    try:
        print(workspace.backup_to(args.output))
    finally:
        workspace.close()
