"""Bounded, observable-only uploads; never extract user-provided archive paths."""
import base64
import csv
import hashlib
import json
from dataclasses import asdict
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import pandas as pd

from src.analysis_inputs import NETWORK_FIELDS, OBSERVATION_FIELDS, validate_frames
from src.data_sources.base import SourceMetadata
from src.data_sources.frames import content_hash

ENTITIES = tuple(NETWORK_FIELDS) + tuple(OBSERVATION_FIELDS)
FILES = {name + ".csv" for name in ENTITIES} | {"dataset.json"}
MAX_BYTES = 15 * 1024 * 1024
MAX_EXPANDED = 60 * 1024 * 1024
LIMITS = {"road_segment": 2500, "road_node": 3000, "turn_restriction": 1000,
          "trajectory_point": 150000, "user_feedback": 2000}
BOOLS = {"routing_eligible", "is_boundary", "oneway", "is_synthetic"}


def decode_bundle(encoded):
    if not isinstance(encoded, str) or len(encoded) > MAX_BYTES * 4 // 3 + 8:
        raise ValueError("V2 数据包不得超过 15 MB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("无法读取数据包编码") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError("V2 数据包不得超过 15 MB")
    return raw


def read_bundle(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("V2 数据包不得超过 15 MB")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) != len(FILES) or {v.filename for v in entries} != FILES:
                raise ValueError("数据包必须恰好包含 dataset.json 和五个规定名称的 CSV；不接受子目录或注入答案")
            if sum(v.file_size for v in entries) > MAX_EXPANDED:
                raise ValueError("数据包解压后不得超过 60 MB")
            if any(v.flag_bits & 1 or (v.external_attr >> 16) & 0o170000 == 0o120000 for v in entries):
                raise ValueError("不接受加密文件或符号链接")
            if archive.getinfo("dataset.json").file_size > 8192:
                raise ValueError("数据集描述过大")
            blobs = {name: archive.read(name) for name in FILES}
    except (BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError("无法读取 ZIP 数据包") from exc
    description = json.loads(blobs["dataset.json"])
    if not isinstance(description, dict) or set(description) != {"label", "network_version", "synthetic"}:
        raise ValueError("dataset.json 必须包含 label、network_version、synthetic")
    for key in ("label", "network_version"):
        value = description[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ValueError("数据集名称和网络版本须为 1–80 字符")
    flags = description["synthetic"]
    if not isinstance(flags, dict) or set(flags) != set(ENTITIES) or any(type(v) is not bool for v in flags.values()):
        raise ValueError("须逐项声明五类输入是否为合成数据")
    digest = hashlib.sha256(raw).hexdigest()
    frames, metadata = {}, {}
    for name in ENTITIES:
        text = blobs[name + ".csv"].decode("utf-8-sig")
        header = next(csv.reader(StringIO(text)), [])
        required = NETWORK_FIELDS.get(name, OBSERVATION_FIELDS.get(name))
        if len(header) != len(set(header)) or not required <= set(header) <= required | {"is_synthetic"}:
            raise ValueError(f"{name} 的字段缺失、重复或包含不支持字段")
        # Preserve identifiers exactly; numerical observations are cleaned downstream.
        frame = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False, nrows=LIMITS[name]+1)
        if len(frame) > LIMITS[name]:
            raise ValueError(f"{name} 超过允许记录数 {LIMITS[name]}")
        for field in BOOLS & set(frame):
            normalized = frame[field].str.lower()
            if not normalized.isin(["true", "false"]).all():
                raise ValueError(f"{name}.{field} 必须明确填写 true 或 false")
            frame[field] = normalized.map({"true": True, "false": False})
        if name in NETWORK_FIELDS:
            for field in {"longitude", "latitude", "node_degree", "length_m", "lanes", "maxspeed"} & set(frame):
                frame[field] = pd.to_numeric(frame[field].replace("", None), errors="raise")
        frames[name] = frame
        metadata[name] = SourceMetadata(
            source_ref=f"uploaded-bundle:{digest}/{name}.csv",
            data_version=digest, network_version=description["network_version"],
            is_synthetic=flags[name],
        )
    if frames["road_segment"].empty or frames["road_node"].empty or frames["trajectory_point"].empty:
        raise ValueError("路网和轨迹不能为空；反馈或转向限制可保留表头并留空")
    nodes = frames["road_node"]
    if not (nodes.longitude.between(121.2, 121.7).all() and nodes.latitude.between(30.9, 31.5).all()
            and nodes.longitude.max()-nodes.longitude.min() <= .06
            and nodes.latitude.max()-nodes.latitude.min() <= .06):
        raise ValueError("当前规则仅支持上海范围内的小样本路网，单边跨度不得超过 0.06 度")
    validate_frames(frames, metadata)
    return {"description": description, "frames": frames, "metadata": metadata,
            "sha256": digest, "raw": raw,
            "preview": {"label": description["label"], "network_version": description["network_version"],
                        "rows": {name: len(frame) for name, frame in frames.items()}, "sha256": digest}}


def write_inputs(bundle, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"entities": {}}
    for name, frame in bundle["frames"].items():
        path = output / (name + ".parquet")
        frame.to_parquet(path, index=False)
        manifest["entities"][name] = {
            "path": path.name, "format": "parquet", "row_count": len(frame),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "content_sha256": content_hash(frame), "metadata": asdict(bundle["metadata"][name]),
        }
    (output / "manifest.json").write_bytes(json.dumps(manifest, ensure_ascii=False).encode())
    return output / "manifest.json"


def example_bundle(source):
    """Package only the five observable entities from an already verified source."""
    from src.domain import InputEntity
    stream = BytesIO()
    flags = {name: source.metadata(InputEntity(name)).is_synthetic for name in ENTITIES}
    description = {"label": "Shanghai-Xuhui-upload-example", "synthetic": flags,
                   "network_version": source.metadata(InputEntity.ROAD_SEGMENT).network_version}
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("dataset.json", json.dumps(description, ensure_ascii=False, indent=2))
        for name in ENTITIES:
            frame = source.read(InputEntity(name)).drop(columns="geometry", errors="ignore")
            archive.writestr(name + ".csv", frame.to_csv(index=False))
    return stream.getvalue()
