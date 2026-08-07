from __future__ import annotations

import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .sqlite_store import SQLiteStore
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

LOW_DISK_THRESHOLD_GB = 5


class StorageManager:
    def __init__(self, db_path: str, vector_path: str, snapshot_dir: str,
                 max_snapshots: int = 10, backup_interval_h: int = 2,
                 enable_wal: bool = True):
        self.sqlite = SQLiteStore(db_path, enable_wal)
        self.vector = VectorStore(vector_path)
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.max_snapshots = max_snapshots
        self.backup_interval_h = backup_interval_h
        self._low_disk_warned = False

    def init(self):
        self.sqlite.connect()
        self.vector.connect()
        self.sqlite.set_delete_callback(self.vector.delete_by_doc_id)
        self.sqlite.ensure_fts5()
        if not self.sqlite.integrity_check():
            logger.error("SQLite integrity check failed, attempting recovery")
            self._try_recover()

    def close(self):
        self.sqlite.close()

    def _try_recover(self):
        snapshot_files = sorted(self.snapshot_dir.glob("*.zip"), reverse=True)
        for snap in snapshot_files:
            try:
                tmp_dir = self.snapshot_dir / "_recovery_tmp"
                tmp_dir.mkdir(exist_ok=True)
                with zipfile.ZipFile(snap, "r") as zf:
                    zf.extractall(tmp_dir)
                recovered_db = tmp_dir / "meta.sqlite"
                if recovered_db.exists():
                    shutil.copy2(recovered_db, self.sqlite.db_path)
                    self.sqlite.close()
                    self.sqlite.connect()
                    if self.sqlite.integrity_check():
                        logger.info("Recovered from snapshot: %s", snap.name)
                        shutil.rmtree(tmp_dir)
                        return
            except Exception as e:
                logger.error("Recovery failed with %s: %s", snap.name, e)
        logger.error("All recovery attempts failed")

    def create_snapshot(self):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snap_name = f"snapshot_{ts}.zip"
        snap_path = self.snapshot_dir / snap_name

        db_file = Path(self.sqlite.db_path)
        vec_dir = Path(self.vector.vector_path)

        with zipfile.ZipFile(snap_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if db_file.exists():
                zf.write(db_file, "meta.sqlite")
            if vec_dir.exists():
                for f in vec_dir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f"vector.lance/{f.name}")

        logger.info("Snapshot created: %s", snap_name)
        self._prune_snapshots()

    def _prune_snapshots(self):
        snaps = sorted(self.snapshot_dir.glob("snapshot_*.zip"))
        while len(snaps) > self.max_snapshots:
            oldest = snaps.pop(0)
            oldest.unlink()
            logger.info("Pruned old snapshot: %s", oldest.name)

    def check_disk_space(self, path: str = None) -> dict:
        check_path = path or str(self.snapshot_dir)
        try:
            usage = shutil.disk_usage(check_path)
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            used_pct = (usage.used / usage.total) * 100
            low = free_gb < LOW_DISK_THRESHOLD_GB
            if low and not self._low_disk_warned:
                logger.warning("Low disk space: %.1f GB free (threshold: %d GB)", free_gb, LOW_DISK_THRESHOLD_GB)
                self._low_disk_warned = True
            elif not low:
                self._low_disk_warned = False
            return {
                "free_gb": round(free_gb, 2),
                "total_gb": round(total_gb, 2),
                "used_pct": round(used_pct, 1),
                "low_disk": low,
            }
        except Exception as e:
            logger.error("Disk space check failed: %s", e)
            return {"free_gb": -1, "total_gb": -1, "used_pct": -1, "low_disk": False}

    def safe_write(self, filepath: str, content: bytes) -> bool:
        disk = self.check_disk_space(str(Path(filepath).parent))
        if disk["low_disk"]:
            logger.error("Disk full, aborting write to %s", filepath)
            return False
        try:
            tmp_path = filepath + ".tmp"
            with open(tmp_path, "wb") as f:
                f.write(content)
            os.replace(tmp_path, filepath)
            return True
        except Exception as e:
            logger.error("Safe write failed for %s: %s", filepath, e)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False
