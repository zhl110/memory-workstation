"""Memory Workstation v2 — PySide6 Desktop Viewer"""
import sys
import os
import logging
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

if not getattr(sys, 'frozen', False):
    os.environ["MW_DEV_DATA_HOME"] = os.path.join(_PROJECT_ROOT, ".memory-workstation-dev")

# ── Logging ───────────────────────────────────────────────────
_log_dir = Path(os.environ.get("MW_DEV_DATA_HOME", ".")) / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(_log_dir / "viewer.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("run")


def main():
    from PySide6.QtWidgets import QApplication
    from src.viewer.file_bridge import DataBridge, _load_config
    from src.viewer.main_window import MainWindow

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MemoryWorkstation.Viewer")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Memory Workstation")
    app.setOrganizationName("MW")

    cfg = _load_config()
    md_dir = cfg.get("md_dir", "D:\\MemoryWorkstation\\.memory-workstation")
    bridge = DataBridge(md_dir)
    w = MainWindow(bridge)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
