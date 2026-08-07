from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    logger.warning("pystray/Pillow not installed, tray UI disabled")

TOAST_DURATION = 3
TOOLTIP_UPDATE_INTERVAL = 5

TRAY_ICON_PATH = Path(__file__).resolve().parent.parent.parent / "tray_icon.png"


def _create_icon(color: str = "gray") -> "Image.Image":
    if not HAS_TRAY:
        return None

    # 优先加载真实 PNG 图标
    icon_path = Path(__file__).resolve().parent.parent.parent / "tray_icon.png"
    if icon_path.exists():
        try:
            return Image.open(icon_path).convert("RGBA").resize((64, 64), Image.LANCZOS)
        except Exception:
            pass

    # 兜底：生成默认图标
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = {
        "gray": (128, 128, 128),
        "blue": (0, 120, 215),
        "orange": (255, 140, 0),
        "red": (220, 50, 50),
    }
    c = colors.get(color, colors["gray"])
    draw.rounded_rectangle([8, 8, 56, 56], radius=8, fill=c)
    draw.text((16, 16), "MW", fill="white")
    return img


class TrayApp:
    def __init__(self, app_context):
        self.ctx = app_context
        self._icon = None
        self._tooltip_timer: Optional[threading.Timer] = None
        self._first_run_guide_shown = False

    def start(self):
        if not HAS_TRAY:
            logger.info("Tray not available, running headless")
            return
        self._icon = pystray.Icon(
            "MemoryWorkstation",
            icon=_create_icon("gray"),
            title="Memory Workstation",
            menu=self._build_menu(),
        )
        threading.Thread(target=self._icon.run, daemon=True).start()
        self._start_tooltip_updater()
        logger.info("Tray icon started")

    def stop(self):
        self._first_run_guide_shown = True
        if self._icon:
            self._icon.stop()

    def set_status(self, color: str):
        if self._icon:
            self._icon.icon = _create_icon(color)

    def show_toast(self, title: str, message: str, color: str = "info"):
        if not HAS_TRAY or not self._icon:
            return
        logger.info("Toast: %s - %s", title, message)
        self._icon.notify(message, title)

    def show_first_run_guide(self):
        if self._first_run_guide_shown:
            return
        self._first_run_guide_shown = True
        cfg = self.ctx.config
        api_host = getattr(cfg.api, 'host', '127.0.0.1')
        api_port = getattr(cfg.api, 'port', 8765)
        guide = (
            "Memory Workstation 已启动！\n\n"
            "功能说明：\n"
            "- 自动扫描全盘文档并AI分类\n"
            "- 通过MCP/HTTP API提供记忆查询\n"
            "- 右键托盘图标查看状态和操作\n\n"
            f"API地址：http://{api_host}:{api_port}\n"
            "Token已自动生成，请查看config.toml"
        )
        self.show_toast("欢迎使用 Memory Workstation", guide)

    def set_autostart(self, enable: bool):
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            exe = sys.executable
            script = str(Path(__file__).parent.parent.parent / "src" / "main.py")
            cmd = f'"{exe}" "{script}"'
            if enable:
                winreg.SetValueEx(key, "MemoryWorkstation", 0, winreg.REG_SZ, cmd)
                logger.info("Autostart enabled")
            else:
                try:
                    winreg.DeleteValue(key, "MemoryWorkstation")
                except FileNotFoundError:
                    pass
                logger.info("Autostart disabled")
            winreg.CloseKey(key)
        except Exception as e:
            logger.error("Failed to set autostart: %s", e)


    @staticmethod
    def _get_config_path() -> Path:
        """Get config.toml in user home ~/.memory-workstation/"""
        from ..core.config import DEFAULT_CONFIG_PATH
        return DEFAULT_CONFIG_PATH

    def _start_tooltip_updater(self):
        def _update():
            while self.ctx._running:
                try:
                    tooltip = self._build_tooltip()
                    if self._icon:
                        self._icon.title = tooltip
                except Exception:
                    pass
                time.sleep(TOOLTIP_UPDATE_INTERVAL)
        t = threading.Thread(target=_update, daemon=True)
        t.start()

    def _build_tooltip(self) -> str:
        parts = ["Memory Workstation"]
        try:
            # V10: 关键词分类模式
            embed_status = "embed:ready" if (self.ctx.llm and self.ctx.llm.has_embed_model) else "embed:unavailable"
            parts.append(f"Mode: keyword | {embed_status}")
        except Exception:
            pass
        try:
            total = self.ctx.storage.sqlite.total_documents() if self.ctx.storage else 0
            parts.append(f"Docs: {total}")
        except Exception:
            pass
        try:
            queue_size = self.ctx.scheduler.classify_queue_size if self.ctx.scheduler else 0
            parts.append(f"Queue: {queue_size}")
        except Exception:
            pass
        try:
            disk = self.ctx.storage.check_disk_space() if self.ctx.storage else {}
            if disk.get("low_disk"):
                parts.append(f"Disk LOW: {disk.get('free_gb', '?')}GB")
        except Exception:
            pass
        return " | ".join(parts)

    def open_control_panel(self, delayed=False):
        """向控制面板发送显示信号"""
        from ..gui.control_panel import request_show

        if delayed:
            def _signal():
                time.sleep(2)
                request_show()
            threading.Thread(target=_signal, daemon=True).start()
        else:
            request_show()

    def _build_menu(self):
        items = [
            pystray.MenuItem("打开控制面板", self._on_open_panel, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("重启服务", self._on_restart),
            pystray.MenuItem("手动全盘扫描", self._on_full_scan),
            pystray.MenuItem("重新整理", self._on_optimize),
            pystray.MenuItem("添加扫描路径", self._on_add_path),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("分类模式: 关键词分类", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("查看当前配置", self._on_show_config),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("锁定模型常驻", self._on_toggle_lock,
                           checked=lambda item: self.ctx.config.global_.lock_model_forever),
            pystray.MenuItem("手动卸载模型", self._on_unload_model),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开机自启", self._on_toggle_autostart,
                           checked=lambda item: self._check_autostart()),
            pystray.MenuItem("重载配置", self._on_reload_config),
            pystray.MenuItem("资源回收", self._on_gc),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开日志文件夹", self._on_open_logs),
            pystray.MenuItem("导出迁移包", self._on_export),
            pystray.MenuItem("检查更新", self._on_check_update),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", self._on_exit),
        ]
        return pystray.Menu(*items)

    def _check_autostart(self) -> bool:
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "MemoryWorkstation")
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def _on_restart(self, item):
        logger.info("Restart requested")
        self.ctx.restart()

    def _on_full_scan(self, item):
        def _full_scan():
            count, pending = self.ctx.scanner.full_scan()
            self.ctx._process_pending_llm(pending)
            self.ctx._export_memories()
        threading.Thread(target=_full_scan, daemon=True).start()

    def _on_optimize(self, item):
        def _run():
            try:
                result = self.ctx.optimizer.run_once()
                if "error" in result:
                    self.show_toast("整理失败", str(result["error"]))
                else:
                    msg = f"衰减{result['decayed']}条, 合并{result['merged']}条, 去重{result['removed']}条"
                    self.show_toast("整理完成", msg)
            except Exception as e:
                self.show_toast("整理失败", str(e))
        threading.Thread(target=_run, daemon=True).start()

    def _on_add_path(self, item):
        import tkinter as tk
        from tkinter import filedialog

        def _dialog():
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askdirectory(title="选择扫描目录")
            root.destroy()
            if path:
                self.ctx.config.scan.custom_white_path.append(path)
                self._save_custom_path(path)
                self.show_toast("路径已添加", f"新增扫描目录: {path}")
                def _scan_new_path():
                    count, pending = self.ctx.scanner.full_scan()
                    self.ctx._process_pending_llm(pending)
                    self.ctx._export_memories()
                threading.Thread(target=_scan_new_path, daemon=True).start()

        threading.Thread(target=_dialog, daemon=True).start()

    def _save_custom_path(self, path: str):
        try:
            import tomllib
            config_path = self._get_config_path()
            if config_path.exists():
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)
                paths = data.get("scan", {}).get("custom_white_path", [])
                if path not in paths:
                    paths.append(path)
                content = config_path.read_text(encoding="utf-8")
                old = "custom_white_path = []"
                import json
                new = f'custom_white_path = {json.dumps(paths, ensure_ascii=False)}'
                if old in content:
                    content = content.replace(old, new)
                    config_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save custom path: %s", e)

    def _apply_provider(self, provider: str = "ollama", api_model: str = ""):
        """V10: LLM 分类已移除，关键词分类无需切换 provider"""
        self.show_toast("分类模式", "V10 已移除 LLM 分类\n当前使用关键词分类模式")

    def _on_test_connection(self, item):
        """V10: LLM 分类已移除，关键词分类无需连接测试"""
        self.show_toast("分类模式", "V10 已移除 LLM 分类\n当前使用关键词分类模式\n无需连接测试")

    def _show_error(self, title: str, message: str):
        """显示错误解释弹窗"""
        def _dialog():
            import tkinter as tk
            from tkinter import messagebox
            
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            messagebox.showerror(f"❌ {title}", message)
            root.destroy()
        
        threading.Thread(target=_dialog, daemon=True).start()
    
    def _on_show_config(self, item):
        """V10: 显示当前分类模式"""
        self.show_toast("当前配置", "分类模式: 关键词分类\n无需外部 LLM 连接")

    def _on_toggle_lock(self, item):
        self.ctx.config.global_.lock_model_forever = not self.ctx.config.global_.lock_model_forever

    def _on_unload_model(self, item):
        self.ctx.llm.unload()

    def _on_toggle_autostart(self, item):
        current = self._check_autostart()
        self.set_autostart(not current)

    def _on_reload_config(self, item):
        from ..core.config import load_config
        self.ctx.config = load_config()
        self.show_toast("配置已重载", "config.toml 已重新加载")

    def _on_gc(self, item):
        import gc
        gc.collect()
        self.show_toast("资源回收", "垃圾回收已执行")

    def _on_open_logs(self, item):
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        subprocess.Popen(["explorer", str(log_dir.resolve())])

    def _on_export(self, item):
        self.ctx.storage.create_snapshot()
        self.show_toast("迁移包已导出", "快照已保存到 memory_storage/snapshot/")

    def _on_check_update(self, item):
        self.show_toast("检查更新", "当前为离线版本，无可用更新")

    def _on_open_panel(self, item):
        self.open_control_panel()

    def _on_exit(self, item):
        self.ctx.shutdown()
