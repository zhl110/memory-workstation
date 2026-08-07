from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def write_crash_dump(error: Exception, log_dir: str = "logs"):
    try:
        Path(log_dir).mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dump_path = Path(log_dir) / f"crash_{ts}.dump"
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(f"Crash Time: {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"Python: {sys.version}\n")
            f.write(f"PID: {os.getpid()}\n")
            f.write(f"Error: {type(error).__name__}: {error}\n\n")
            f.write("Traceback:\n")
            traceback.print_exc(file=f)
        logger.info("Crash dump written: %s", dump_path)
        return dump_path
    except Exception as e:
        logger.error("Failed to write crash dump: %s", e)
        return None


def check_crash_dumps(log_dir: str = "logs") -> list[Path]:
    log_path = Path(log_dir)
    if not log_path.exists():
        return []
    dumps = sorted(log_path.glob("crash_*.dump"), reverse=True)
    return dumps[:5]


def read_latest_crash(log_dir: str = "logs") -> str | None:
    dumps = check_crash_dumps(log_dir)
    if not dumps:
        return None
    try:
        return dumps[0].read_text(encoding="utf-8")
    except Exception:
        return None
