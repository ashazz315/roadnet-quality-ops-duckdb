"""Single-instance persistent workspace with server-side write authorization."""
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from roadinsight_ui.actions import read_reviews, record_review, save_upload
from roadinsight_ui.bundles import read_bundle, write_inputs
from roadinsight_ui.snapshot import load_snapshot

ROOT = Path(__file__).resolve().parents[1]


def configuration(environ=None):
    env = os.environ if environ is None else environ
    mode = env.get("ROADINSIGHT_MODE", "local")
    if mode not in {"local", "server", "community"}:
        raise ValueError("ROADINSIGHT_MODE 必须是 local、server 或 community")
    directory = Path(env.get("ROADINSIGHT_DATA_DIR", str(ROOT / "data/runtime/workspace"))).resolve()
    token = env.get("ROADINSIGHT_EDITOR_TOKEN", "")
    if mode == "server" and (not env.get("ROADINSIGHT_DATA_DIR") or len(token) < 32):
        raise ValueError("在线模式须配置持久化目录 ROADINSIGHT_DATA_DIR 和至少 32 字符的编辑访问码 ROADINSIGHT_EDITOR_TOKEN")
    if mode == "community" and (not env.get("ROADINSIGHT_DATA_DIR") or (token and len(token) < 32)):
        raise ValueError("免费体验模式须配置临时目录；编辑访问码可留空（只读），设置时至少 32 字符")
    return mode, directory, token


def unlock(token, submitted):
    return isinstance(submitted, str) and len(submitted) <= 256 and bool(token) and hmac.compare_digest(
        hashlib.sha256(token.encode()).digest(), hashlib.sha256(submitted.encode()).digest()
    )


def require_editor(mode, authorized):
    if mode != "local" and authorized is not True:
        raise PermissionError("请先解锁编辑工作区；公开访客只能浏览演示数据")


def now():
    return datetime.now(timezone.utc).isoformat()


class Workspace:
    """One server process per persistent volume; SQLite tracks durable job states."""

    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        self._lock = (self.root / ".instance.lock").open("a+b")
        self._lock.seek(0)
        if os.fstat(self._lock.fileno()).st_size == 0:
            self._lock.write(b"0")
            self._lock.flush()
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            raise RuntimeError("此数据目录已有服务运行；每个持久卷只允许一个服务实例") from exc
        self.database = self.root / "workspace.sqlite"
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, finished_at TEXT, message TEXT,
                input_sha256 TEXT NOT NULL, snapshot_id TEXT)""")
            db.execute("UPDATE jobs SET status='interrupted', finished_at=?, message=? WHERE status='running'",
                       (now(), "服务重启中断了分析；已保留原始输入，请重新提交"))

    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def close(self):
        if not self._lock.closed:
            if os.name == "nt":
                import msvcrt
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
            self._lock.close()

    def jobs(self):
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 50")]

    def job_path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("无效的分析任务")
        return self.root / "jobs" / job_id

    def snapshot(self, job_id):
        path = self.job_path(job_id)
        with closing(self.connect()) as db:
            row = db.execute("SELECT status,snapshot_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] != "completed":
            raise ValueError("此任务尚未成功完成，不能切换结果")
        result = load_snapshot(path / "snapshot.json.gz")
        if result["snapshot_id"] != row["snapshot_id"]:
            raise ValueError("任务结果指纹不一致")
        return result

    def run(self, raw, request_id, *, timeout=300):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("无效的提交标识")
        if not self.mutex.acquire(blocking=False):
            raise ValueError("工作区正在处理另一项操作，请稍后刷新再试")
        try:
            with closing(self.connect()) as db:
                existing = db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
                if existing:
                    return dict(existing)
                if db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= 50:
                    raise ValueError("工作区已达到 50 个任务上限，请先由管理员备份和归档")
            if shutil.disk_usage(self.root).free < 500 * 1024 * 1024:
                raise ValueError("剩余存储不足 500 MB，请扩容或归档后再试")
            bundle = read_bundle(raw)
            job_id = uuid.uuid4().hex
            path = self.job_path(job_id)
            path.mkdir(parents=True)
            (path / "upload.zip").write_bytes(raw)
            (path / "dataset.json").write_bytes(json.dumps(bundle["description"], ensure_ascii=False).encode())
            write_inputs(bundle, path / "inputs")
            with closing(self.connect()) as db, db:
                db.execute("INSERT INTO jobs (id,request_id,label,status,created_at,input_sha256) VALUES (?,?,?,'running',?,?)",
                           (job_id, request_id, bundle["description"]["label"], now(), bundle["sha256"]))
            try:
                # The worker needs no editor credentials or hosting credentials.
                allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH"}
                worker_env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
                worker_env.update({"PYTHONIOENCODING": "utf-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
                with (path / "worker.log").open("wb") as log:
                    subprocess.run([sys.executable, "-X", "faulthandler", str(ROOT / "scripts/11_run_uploaded.py"), "--job", str(path)],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=worker_env,
                                   timeout=timeout, check=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                snapshot = load_snapshot(path / "snapshot.json.gz")
                with closing(self.connect()) as db, db:
                    db.execute("UPDATE jobs SET status='completed',finished_at=?,snapshot_id=?,message=? WHERE id=?",
                               (now(), snapshot["snapshot_id"], "检测与假设回放已完成；没有独立标注，准确率未计算", job_id))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                with (path / "worker.log").open("a", encoding="utf8") as log:
                    log.write(f"\nWorker failed: {type(exc).__name__}; returncode={getattr(exc, 'returncode', None)}\n")
                with closing(self.connect()) as db, db:
                    db.execute("UPDATE jobs SET status='failed',finished_at=?,message=? WHERE id=?",
                               (now(), "分析失败或超时；输入及私有运行日志已保留，没有替换当前结果", job_id))
            with closing(self.connect()) as db:
                return dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        finally:
            self.mutex.release()

    def review(self, snapshot, value):
        with self.mutex:
            return record_review(snapshot, value.get("issue_id"), value.get("conclusion"),
                                 value.get("reviewer", ""), value.get("note", ""),
                                 self.root / "reviews", value["event_id"])

    def reviews(self, snapshot):
        with self.mutex:
            return read_reviews(snapshot, self.root / "reviews")

    def save_legacy(self, upload):
        with self.mutex:
            return save_upload(upload, self.root / "uploads.duckdb")

    def backup_to(self, output):
        """Consistent backup under the same write lock; secrets are outside this tree."""
        output = Path(output).resolve()
        if output.is_relative_to(self.root) or output.exists():
            raise ValueError("备份须写入工作区外的新文件")
        output.parent.mkdir(parents=True, exist_ok=True)
        with self.mutex, tempfile.TemporaryDirectory(dir=output.parent) as staging:
            copy = Path(staging) / "workspace.sqlite"
            with closing(self.connect()) as source, closing(sqlite3.connect(copy)) as target:
                source.backup(target)
            with ZipFile(output, "x", ZIP_DEFLATED) as archive:
                archive.write(copy, "workspace.sqlite")
                for path in sorted(self.root.rglob("*")):
                    if path.is_symlink():
                        raise ValueError("工作区中含有符号链接，拒绝备份")
                    if path.is_file() and path.name not in {".instance.lock", "workspace.sqlite", "workspace.sqlite-journal"}:
                        archive.write(path, path.relative_to(self.root).as_posix())
        return output
