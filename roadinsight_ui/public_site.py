"""Export only reviewed presentation assets and bound demo results."""

import hashlib
import json
import shutil
from pathlib import Path

from roadinsight_ui.snapshot import DEFAULT_SNAPSHOT, load_snapshot
from src.evaluation.report import bind_report, read_report

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    "styles.css", "model.js", "icons.js", "maps.js", "validation.js", "app.js",
    "public.js", "backend.js", "assets/shanghai-skyline.png", "vendor/leaflet.css",
    "vendor/leaflet.js", "vendor/LEAFLET-LICENSE", "vendor/manifest.json",
)


def build_public_site(output, snapshot=DEFAULT_SNAPSHOT,
                      report=ROOT / "data/validation/default_report.json", revision="local"):
    output = Path(output).resolve()
    # Never overwrite or clean an existing directory; stale files must not leak.
    if output.exists() and any(output.iterdir()):
        raise ValueError("Public output must be a new or empty directory")
    payload = load_snapshot(snapshot)
    validation = bind_report(read_report(report), payload)
    frontend = ROOT / "roadinsight_ui/frontend"
    for name in ASSETS:
        if not (frontend / name).is_file():
            raise ValueError(f"Missing public asset: {name}")
    output.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(frontend / name, target)
    html = (frontend / "index.html").read_text(encoding="utf-8")
    html = html.replace('<script src="app.js">', '<script src="public.js"></script><script src="app.js">')
    html = html.replace('<title>RoadInsight</title>', '<title>RoadInsight V2 · 路网诊断演示</title><meta name="description" content="路网质量诊断、证据分析、假设路线重放与独立基准验证。公开只读作品集，基于 OSM 和合成观测。">')
    (output / "index.html").write_bytes(html.encode())
    (output / ".nojekyll").write_bytes(b"")
    files = {}
    for name, value in (("snapshot.json", payload), ("validation.json", validation)):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        (output / name).write_bytes(raw)
        files[name] = hashlib.sha256(raw).hexdigest()
    manifest = {"schema_version": 1, "revision": revision,
                "snapshot_id": payload["snapshot_id"], "files": files}
    (output / "release.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode())
    return manifest
